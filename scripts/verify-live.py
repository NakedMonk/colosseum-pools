#!/usr/bin/env python3
"""The site as it is served, checked against the pack it was uploaded from.

    python3 scripts/verify-live.py <pack>.sha256 https://pools.usenami.io
    python3 scripts/verify-live.py <pack>.sha256 <a folder standing in for the site>

The list is the one scripts/pack-site.sh writes beside the pack: a sha256 for every file of the pack, build.json
included. Every file in it is read from the site and hashed. Exit 0 only if every one matches; 1 if any differs or
cannot be read, each named on a line of its own; 2 if the arguments or the list cannot be used.

index.html is read from "/", the address a visitor opens. Cloudflare inserts two tags into it before </body>, in
either order: its challenge script, and its Web Analytics beacon followed by a newline. Each is cut on its own, so
the rest has to match the pack byte for byte. Nothing else is cut: anything more on the page is a mismatch.
"""
import hashlib
import os
import re
import sys
import time
import urllib.parse
import urllib.request

CHALLENGE = re.compile(
    rb"<script>\(function\(\)\{function c\(\)\{.*?/cdn-cgi/challenge-platform/scripts/jsd/main\.js.*?</script>", re.S)
BEACON = re.compile(rb"<script[^>]*static\.cloudflareinsights\.com/beacon\.min\.js[^>]*>\s*</script>\n?")
LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


def entries(path):
    """The list's (sha256, file) pairs, with shasum's "./" taken off; a line of anything else is refused."""
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            m = LINE.match(line)
            rel = m.group(2).removeprefix("./") if m else ""
            if not m or rel.startswith("/") or ".." in rel.split("/"):
                raise ValueError(f"{path}:{n} is not a sha256 line of a file in the pack: {line[:80]!r}")
            out.append((m.group(1), rel))
    if not out:
        raise ValueError(f"{path} names no file: an empty list would pass any site")
    return out


def reader(base):
    """How the site is read: over HTTP(S) with a cache-buster, or from a folder on disk."""
    if base.startswith(("https://", "http://")):
        def get(rel):
            path = "" if rel == "index.html" else urllib.parse.quote(rel)
            req = urllib.request.Request(f"{base}/{path}?v={time.time_ns() // 1_000_000}",
                                         headers={"User-Agent": "pack-verify", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(), f"{r.status} {r.headers.get('Content-Type', '')}"
        return get, "index.html from /"
    if not os.path.isdir(base):
        raise ValueError(f"{base} is neither an http(s) address nor a folder")

    def read(rel):
        with open(os.path.join(base, rel), "rb") as f:
            return f.read(), "file"
    return read, "index.html"


def main(argv):
    if len(argv) != 3:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    sums, base = argv[1], argv[2].rstrip("/")
    try:
        files = entries(sums)
        get, index_from = reader(base)
    except (OSError, ValueError) as e:
        print(f"verify-live: {e}", file=sys.stderr)
        return 2
    print("read at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    match = differ = 0
    notes = []
    for want, rel in files:
        try:
            body, how = get(rel)
        except Exception as e:  # a refused or failed read is a file the site does not serve as packed
            differ += 1
            print("ERR ", rel, e)
            continue
        if rel == "index.html":
            n_challenge, n_beacon = len(CHALLENGE.findall(body)), len(BEACON.findall(body))
            body = BEACON.sub(b"", CHALLENGE.sub(b"", body))
            notes.append(f"{index_from}: cut Cloudflare's challenge script x{n_challenge} and Web Analytics beacon "
                         f"x{n_beacon} before comparing")
        if hashlib.sha256(body).hexdigest() == want:
            match += 1
        else:
            differ += 1
            print("DIFF", rel, how, len(body))
    print(f"live {base}: match={match} differ={differ} of {match + differ}")
    for note in notes:
        print(note)
    return 0 if differ == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
