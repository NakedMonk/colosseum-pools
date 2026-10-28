"""A pool owner's contacts for its traders and co-investors: `GET /v1/contacts?pool=0x…` answers the last
record the pool's owner signed, and `POST /v1/contacts {"message": "…", "signature": "0x…"}` replaces it
(Alex's word to Grok, the CTO's design on 28 Oct 2026).

Nothing of it is on chain: v3 is frozen and a pool's Plan and Rules have no line for it. The owner signs,
with the wallet that owns the pool, the message itself, a JSON object of

    {"pool": "0x…", "discord": "…", "telegram": "…", "x": "…", "email": "…", "nonce": 1793140000000}

as a plain message (EIP-191, personal_sign), and the gateway keeps the signed text and the signature, so
anyone can check the record without trusting it. Every contact is optional; an empty string is none.

What it refuses, in words and with nothing kept:
- a message that is not that object, carries another key or one key twice, or is longer than MAX_MESSAGE;
- a contact that is not in its one accepted form (FORMS): a link on discord.gg, discord.com/invite,
  t.me, x.com or twitter.com with nothing after the name, or a bare email address, which the page shows
  as mailto:;
- a nonce that is not a whole number above the pool's last one, or more than NONCE_AHEAD_MS ahead of
  the gateway's clock: one far in the future would lock the owner out of their own record;
- a signature that does not recover;
- an address this gateway's factory did not make (isPool), and a signer that is not the pool's owner.

The checks that read nothing come first; the chain is read last, once a pool: its owner is set when it
is made and never changes (src/Pool.sol, initialize), so both answers are kept for the process's life.

Every record kept is a line of contacts.jsonl in the gateway's state directory, never rewritten; the
pool's last record is the line with its highest nonce, read back from the file at the start, so a
restart forgets nothing. A line is a record once it has its end: what a crash or a full disk left after
the last whole line was never answered, and the next record is written over it. The links are checked again where the page shows them (app/lib/contacts.js):
a record that slipped past this file still shows no other link.

Testnet only, as the rest of the gateway.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address

from .chain import Throttled

FIELDS = ("discord", "telegram", "x", "email")
KEYS = frozenset(("pool", "nonce", *FIELDS))
MAX_MESSAGE = 1024
MAX_FIELD = 200
# A nonce is the owner's clock in milliseconds, as the app makes it (Date.now()); this much ahead of the
# gateway's is a wrong clock, not a later record.
NONCE_AHEAD_MS = 10 * 60 * 1000
MAX_NONCE = 2**53 - 1  # what a browser's number holds exactly
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
SIGNATURE = re.compile(r"0x[0-9a-fA-F]{130}")
# Each contact's one accepted form, matched whole: the hosts the CTO listed, the scheme https alone, and
# nothing after the name, no query, no fragment, no further path.
FORMS = {
    "discord": re.compile(r"https://(?:discord\.gg/|discord\.com/invite/)[A-Za-z0-9-]{2,32}"),
    "telegram": re.compile(r"https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{4,31}|\+[A-Za-z0-9_-]{8,40})"),
    "x": re.compile(r"https://(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}"),
}


class Refused(Exception):
    """A record refused, and nothing kept. 4xx only: the CDN in front replaces the body of a 5xx with its
    own page (gateway/server.py, `not_sent`), and then nobody reads why."""

    def __init__(self, status: int, code: str, detail: str, **extra: Any):
        super().__init__(code)
        self.status, self.code, self.detail, self.extra = status, code, detail, extra

    def answer(self) -> tuple[int, dict]:
        return self.status, {"status": "refused_by_contacts", "code": self.code, "detail": self.detail,
                             "kept": False, **self.extra}


def parse_pool(raw: Any) -> str:
    """The pool's address, checksummed. One written in mixed case must be a valid checksum: a typo in
    it is another address."""
    if not isinstance(raw, str) or not ADDRESS.fullmatch(raw):
        raise Refused(400, "bad_pool", "the pool is an address of 40 hex digits")
    pool = to_checksum_address(raw)
    if raw[2:] != raw[2:].lower() and raw[2:] != raw[2:].upper() and raw != pool:
        raise Refused(400, "bad_pool", "the pool's capitals are not its checksum; copy it again")
    return pool


def parse_contacts(fields: dict) -> dict:
    """The four contacts, each "" or in its form."""
    out = {}
    for name in FIELDS:
        value = fields.get(name, "")
        if not isinstance(value, str) or len(value) > MAX_FIELD:
            raise Refused(400, "bad_contact", f"{name} is a string of at most {MAX_FIELD} characters", field=name)
        if value and not FORMS[name].fullmatch(value):
            raise Refused(400, "bad_contact", f"{name} is not in a form this site shows", field=name)
        out[name] = value
    return out


def _once(pairs: list) -> dict:
    """An object whose keys are each there once: with one key twice, two readers of the same signed text
    could each take another value."""
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("a key twice")
    return dict(pairs)


def parse_message(body: Any, now_ms: int) -> tuple[str, str, dict, int, str]:
    """(the signed text, the pool, its contacts, the nonce, the signer), every check that reads nothing."""
    message = body.get("message") if isinstance(body, dict) else None
    signature = body.get("signature") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message or len(message) > MAX_MESSAGE:
        raise Refused(400, "bad_message", f'send {{"message": "…", "signature": "0x…"}}, the message at most '
                      f"{MAX_MESSAGE} characters")
    try:
        fields = json.loads(message, object_pairs_hook=_once)
    except (ValueError, RecursionError):  # brackets nested deep enough stop the parser on Python 3.12
        fields = None
    if not isinstance(fields, dict) or not {"pool", "nonce"} <= set(fields) or not set(fields) <= KEYS:
        raise Refused(400, "bad_message", "the message is a JSON object of pool and nonce, and of discord, "
                      "telegram, x and email where given, and of nothing else")
    pool = parse_pool(fields["pool"])
    contacts = parse_contacts(fields)
    nonce = fields["nonce"]
    if isinstance(nonce, bool) or not isinstance(nonce, int) or not 0 < nonce <= MAX_NONCE:
        raise Refused(400, "bad_nonce", "the nonce is a whole number above 0, the time in milliseconds")
    if nonce > now_ms + NONCE_AHEAD_MS:
        raise Refused(400, "bad_nonce", "the nonce is ahead of the gateway's clock; check the device's clock")
    if not isinstance(signature, str) or not SIGNATURE.fullmatch(signature):
        raise Refused(400, "bad_signature", "the signature is 65 bytes in hex, as the wallet gives it")
    try:
        signer = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception:
        raise Refused(400, "bad_signature", "the signature does not recover to any address") from None
    return message, pool, contacts, nonce, to_checksum_address(signer)


class Book:
    """The records on disk: one line each in contacts.jsonl, and in memory each pool's last."""

    def __init__(self, directory: str | pathlib.Path):
        self.journal = pathlib.Path(directory) / "contacts.jsonl"
        self._last: dict[str, dict] = {}
        try:
            raw = self.journal.read_bytes()
        except FileNotFoundError:
            raw = b""
        except OSError as exc:
            raise SystemExit(f"refusing to start: {self.journal} cannot be read ({exc})") from None
        # Where the last whole line ends: the next record is written from here (keep).
        self._end = raw.rfind(b"\n") + 1
        try:
            lines = raw[:self._end].decode("utf-8").split("\n")[:-1]
        except UnicodeDecodeError as exc:
            raise SystemExit(f"refusing to start: {self.journal} cannot be read ({exc})") from None
        for i, line in enumerate(lines):
            try:
                entry = json.loads(line)
                pool, nonce = entry["pool"], entry["nonce"]
                if not isinstance(pool, str) or isinstance(nonce, bool) or not isinstance(nonce, int):
                    raise TypeError("not a pool and a nonce")
            except (ValueError, KeyError, TypeError):
                # A whole line that is not a record is the file's damage, and starting past it would serve
                # an older record as the last.
                raise SystemExit(f"refusing to start: line {i + 1} of {self.journal} is not a record") from None
            if pool not in self._last or nonce > self._last[pool]["nonce"]:
                self._last[pool] = entry

    def last(self, pool: str) -> dict | None:
        return self._last.get(pool)

    def keep(self, entry: dict) -> None:
        """The record as a line right after the last whole one, on the disk before it is answered. What a
        write cut short left there is written over, so it never joins the next record into a line that is
        not one (CodeRabbit, cp#184)."""
        line = (json.dumps(entry) + "\n").encode("utf-8")
        fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT, 0o666)
        try:
            os.ftruncate(fd, self._end)
            os.lseek(fd, self._end, os.SEEK_SET)
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except OSError:
            with contextlib.suppress(OSError):
                os.ftruncate(fd, self._end)  # a part of a line is no record either
            raise
        finally:
            os.close(fd)
        self._end += len(line)
        self._last[entry["pool"]] = entry


class Contacts:
    def __init__(self, reader: Any, book: Book, clock: Callable[[], float] = time.time,
                 log: Callable[..., None] | None = None):
        self.reader = reader
        self.book = book
        self.clock = clock
        self.log = log or (lambda **_: None)
        self._pools: set[str] = set()  # made by this factory: it stays so
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def read(self, raw_pool: str) -> tuple[int, dict]:
        try:
            pool = parse_pool(raw_pool)
        except Refused as r:
            return r.answer()
        entry = self.book.last(pool)
        if entry is None:
            return 404, {"status": "not_found", "code": "no_contacts", "detail": "this pool's owner has left none",
                         "pool": pool}
        return 200, {"status": "ok", **entry}

    def write(self, body: Any) -> tuple[int, dict]:
        try:
            message, pool, contacts, nonce, signer = parse_message(body, int(self.clock() * 1000))
        except Refused as r:
            return r.answer()
        try:
            self._fresh(pool, nonce)
            # Outside the lock: a slow node holds up this pool's owner alone, not every other pool's write.
            owner = self._owner(pool)
            if signer != owner:
                raise Refused(403, "not_owner", "the signature is not the pool owner's", signer=signer)
            # One record at a time: the nonce is checked again against the last, which may have moved meanwhile.
            with self._lock:
                self._fresh(pool, nonce)
                return self._keep(message, pool, contacts, nonce, signer, body["signature"])
        except Refused as r:
            return r.answer()

    def _fresh(self, pool: str, nonce: int) -> None:
        last = self.book.last(pool)
        if last is not None and nonce <= last["nonce"]:
            raise Refused(409, "stale_nonce", "the pool has a record with this nonce or a later one; sign again")

    def _keep(self, message: str, pool: str, contacts: dict, nonce: int, signer: str, signature: str) -> tuple[int, dict]:
        when = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"t": when, "pool": pool, "contacts": contacts, "nonce": nonce, "signer": signer,
                 "message": message, "signature": signature}
        try:
            self.book.keep(entry)
        except OSError as exc:
            self.log(event="contacts_not_kept", pool=pool, error=type(exc).__name__)
            raise Refused(424, "not_kept", "the gateway could not write the record; nothing was kept, "
                          "send it again") from None
        self.log(event="contacts_kept", pool=pool, nonce=nonce, given=[k for k in FIELDS if contacts[k]])
        return 200, {"status": "kept", "kept": True, "pool": pool, "nonce": nonce, "contacts": contacts}

    def _owner(self, pool: str) -> str:
        """The pool's owner, once this factory is known to have made it: read once and kept."""
        try:
            if pool not in self._pools:
                if not self.reader.is_pool(pool):
                    raise Refused(404, "not_a_pool", "this deployment's factory did not make that pool")
                self._pools.add(pool)
            if pool not in self._owners:
                self._owners[pool] = to_checksum_address(self.reader.pool_owner(pool))
        except Throttled:
            raise Refused(429, "upstream_busy", "the chain node is refusing reads right now; nothing was kept, "
                          "ask again in a moment", retry_after_s=30) from None
        except Refused:
            raise
        except Exception as exc:
            self.log(event="contacts_read_failed", error=type(exc).__name__)
            raise Refused(424, "upstream_failed", "a read of the chain failed; nothing was kept") from None
        return self._owners[pool]


def is_contacts_state(entry: str) -> bool:
    """A state directory systemd made for the contacts: contacts for the first deployment's gateway
    (ops/host/colosseum-gateway.service), contacts-<instance> for a further one's."""
    name = os.path.basename(entry.rstrip("/"))
    return name == "contacts" or name.startswith("contacts-")


def contacts_directory(env) -> str | None:
    """Where the records go: GATEWAY_CONTACTS_DIR, or the service's state directory systemd made for
    them; None for neither."""
    named = env.get("GATEWAY_CONTACTS_DIR")
    if named:
        return named
    for entry in env.get("STATE_DIRECTORY", "").split(":"):
        if entry and is_contacts_state(entry):
            return entry
    return None


def contacts_from_env(env, reader: Any, log: Callable[..., None] | None = None) -> Contacts | None:
    """The owners' contacts, where the gateway has a directory for them; None, and 404 on the path, without."""
    directory = contacts_directory(env)
    if directory is None:
        return None
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        raise SystemExit(f"refusing to start: the contacts' directory {directory} is not one this gateway "
                         "can write (GATEWAY_CONTACTS_DIR, docs/HOSTING.md)")
    return Contacts(reader, Book(directory), log=log)
