"""A pool owner's contacts (gateway/contacts.py): what the owner keeps, everything it refuses, that a refusal
keeps nothing, and that a restart forgets nothing."""

import json
import os
import pathlib
import tempfile
import threading
import unittest

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address

from gateway.chain import JsonRpcReader, Throttled
from gateway.contacts import (FIELDS, MAX_MESSAGE, NONCE_AHEAD_MS, Book, Contacts, contacts_directory,
                              contacts_from_env)

# Keys made for these tests from a phrase: they hold nothing anywhere.
OWNER = Account.from_key(keccak(text="colosseum-pools contacts test: the owner"))
STRANGER = Account.from_key(keccak(text="colosseum-pools contacts test: a stranger"))
POOL = to_checksum_address("0x69e88dbe895afbbd773cb60a09d1113777a6dfb0")
OTHER_POOL = to_checksum_address("0x5c1e9a7b3d2f4e6a8b0c1d2e3f4a5b6c7d8e9f01")
NOW = 1_793_140_000  # 28 Oct 2026, 19:06 UTC
NOW_MS = NOW * 1000
# The at sign is put back where the cases file writes {at}, so the repository holds no e-mail address.
CASES = json.loads((pathlib.Path(__file__).parent / "contacts-cases.json").read_text().replace("{at}", "@"))
EMAIL = "@".join(("team", "example.org"))
GIVEN = {"discord": "https://discord.gg/abc123", "telegram": "https://t.me/example_pool",
         "x": "https://x.com/example", "email": EMAIL}


class Reader:
    """The chain as the contacts see it: which pools the factory made and who owns each, and every read."""

    def __init__(self):
        self.pools = {POOL: OWNER.address, OTHER_POOL: STRANGER.address}
        self.reads = []
        self.error = None

    def is_pool(self, address):
        self.reads.append(("is_pool", address))
        if self.error is not None:
            raise self.error
        return address in self.pools

    def pool_owner(self, pool):
        self.reads.append(("pool_owner", pool))
        if self.error is not None:
            raise self.error
        return self.pools[pool]


def signed(fields, key=OWNER):
    message = json.dumps(fields)
    signature = key.sign_message(encode_defunct(text=message)).signature.hex()
    return {"message": message, "signature": signature if signature.startswith("0x") else "0x" + signature}


def record(nonce=NOW_MS, pool=POOL, key=OWNER, **contacts):
    return signed({"pool": pool, **contacts, "nonce": nonce}, key)


class ContactsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.reader = Reader()
        self.contacts = Contacts(self.reader, Book(self.dir), clock=lambda: NOW)

    def lines(self):
        path = pathlib.Path(self.dir) / "contacts.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def refused(self, body, status, code):
        before = self.lines()
        got, out = self.contacts.write(body)
        self.assertEqual((got, out["code"], out["kept"]), (status, code, False), out)
        self.assertEqual(self.lines(), before, "a refusal keeps nothing")
        return out


class WhatTheOwnerKeeps(ContactsTest):
    def test_the_owner_keeps_a_record_and_anyone_reads_it_back(self):
        body = record(**GIVEN)
        status, out = self.contacts.write(body)
        self.assertEqual((status, out["status"], out["contacts"]), (200, "kept", GIVEN))
        status, got = self.contacts.read(POOL.lower())
        self.assertEqual((status, got["pool"], got["contacts"], got["nonce"]), (200, POOL, GIVEN, NOW_MS))
        # The record carries what was signed, so anyone can check it without trusting the gateway.
        self.assertEqual((got["message"], got["signature"]), (body["message"], body["signature"]))
        self.assertEqual(Account.recover_message(encode_defunct(text=got["message"]), signature=got["signature"]),
                         OWNER.address)
        self.assertEqual(len(self.lines()), 1)

    def test_every_contact_is_optional_and_empty_is_none(self):
        self.assertEqual(self.contacts.write(record())[1]["contacts"], {k: "" for k in FIELDS})
        self.assertEqual(self.contacts.write(record(NOW_MS + 1, x="https://x.com/example", email=""))[1]["contacts"],
                         {"discord": "", "telegram": "", "x": "https://x.com/example", "email": ""})

    def test_a_later_record_replaces_the_last(self):
        self.contacts.write(record(**GIVEN))
        self.contacts.write(record(NOW_MS + 5, telegram="https://t.me/+AbCdEf123456"))
        got = self.contacts.read(POOL)[1]
        self.assertEqual((got["nonce"], got["contacts"]["telegram"], got["contacts"]["discord"]),
                         (NOW_MS + 5, "https://t.me/+AbCdEf123456", ""))

    def test_a_pool_without_a_record(self):
        status, out = self.contacts.read(POOL)
        self.assertEqual((status, out["code"], out["pool"]), (404, "no_contacts", POOL))
        self.assertEqual(self.contacts.read("0x123")[1]["code"], "bad_pool")


class WhatItRefuses(ContactsTest):
    def test_each_contact_in_its_one_form(self):
        for field, values in CASES["accept"].items():
            for value in values:
                with self.subTest(field=field, value=value):
                    status, out = self.contacts.write(record(NOW_MS + len(self.lines()), **{field: value}))
                    self.assertEqual((status, out["contacts"][field]), (200, value))
        for field, values in CASES["refuse"].items():
            for value in values:
                with self.subTest(field=field, value=value):
                    out = self.refused(record(NOW_MS + 100, **{field: value}), 400, "bad_contact")
                    self.assertEqual(out["field"], field)
        self.refused(record(NOW_MS + 100, x=["https://x.com/example"]), 400, "bad_contact")
        self.refused(record(NOW_MS + 100, email="@".join(("a" * 190, "example.org"))), 400, "bad_contact")

    def test_a_message_of_other_keys_or_not_an_object(self):
        for body in (signed({"pool": POOL, "nonce": NOW_MS, "website": "https://example.org"}),
                     signed({"pool": POOL}), signed({"nonce": NOW_MS}), signed([POOL, NOW_MS]),
                     {"message": "{nope", "signature": record()["signature"]},
                     signed({"pool": POOL, "nonce": NOW_MS})
                     | {"message": f'{{"pool": "{POOL}", "pool": "{OTHER_POOL}", "nonce": {NOW_MS}}}'},
                     {"message": json.dumps({"pool": POOL, "nonce": NOW_MS, "x": "x" * MAX_MESSAGE})},
                     {"message": {"pool": POOL, "nonce": NOW_MS}}, {"message": ""}, ["message"], None):
            with self.subTest(body=str(body)[:60]):
                self.refused(body, 400, "bad_message")

    def test_a_bad_pool_address(self):
        i = next(i for i, c in enumerate(POOL) if i > 1 and c.isalpha())
        wrong_case = POOL[:i] + POOL[i].swapcase() + POOL[i + 1:]
        for pool in ("0x123", "69e88dbe895afbbd773cb60a09d1113777a6dfb0", 7, wrong_case):
            with self.subTest(pool=pool):
                self.refused(record(pool=pool), 400, "bad_pool")

    def test_a_nonce_that_is_not_a_whole_number(self):
        for nonce in ("1793140000000", 1.5, True, 0, -1, 2**53):
            with self.subTest(nonce=nonce):
                self.refused(record(nonce), 400, "bad_nonce")

    def test_a_nonce_ahead_of_the_clock_is_refused(self):
        self.refused(record(NOW_MS + NONCE_AHEAD_MS + 1), 400, "bad_nonce")
        self.assertEqual(self.contacts.write(record(NOW_MS + NONCE_AHEAD_MS))[0], 200)

    def test_a_nonce_not_above_the_last_is_refused(self):
        self.contacts.write(record(**GIVEN))
        self.refused(record(NOW_MS, x="https://x.com/example"), 409, "stale_nonce")
        self.refused(record(NOW_MS - 1), 409, "stale_nonce")
        self.assertEqual(self.contacts.read(POOL)[1]["contacts"], GIVEN)

    def test_a_signature_that_does_not_recover_or_is_not_hex(self):
        body = record()
        for signature in ("0x" + "00" * 65, "nope", body["signature"][:-2], 7, None):
            with self.subTest(signature=signature):
                self.refused({"message": body["message"], "signature": signature}, 400, "bad_signature")

    def test_a_signer_who_is_not_the_owner(self):
        out = self.refused(record(key=STRANGER, **GIVEN), 403, "not_owner")
        self.assertEqual(out["signer"], STRANGER.address)
        # The signature is good, but for another message: the record names a pool its signer does not own.
        self.refused(record(pool=OTHER_POOL), 403, "not_owner")
        self.assertEqual(self.contacts.read(POOL)[0], 404)

    def test_an_address_the_factory_did_not_make(self):
        self.refused(record(pool=to_checksum_address("0x" + "ab" * 20)), 404, "not_a_pool")


class TheChain(ContactsTest):
    def test_checks_that_read_nothing_come_before_the_chain(self):
        self.refused(record(x="https://x.com/"), 400, "bad_contact")
        self.refused(record(NOW_MS + NONCE_AHEAD_MS + 1), 400, "bad_nonce")
        self.refused({"message": record()["message"], "signature": "0x" + "00" * 65}, 400, "bad_signature")
        self.assertEqual(self.reader.reads, [])
        self.contacts.write(record())
        self.reader.reads.clear()
        self.refused(record(NOW_MS), 409, "stale_nonce")
        self.assertEqual(self.reader.reads, [])

    def test_the_chain_is_read_once_a_pool(self):
        for n in range(3):
            self.assertEqual(self.contacts.write(record(NOW_MS + n))[0], 200)
        self.assertEqual(self.reader.reads, [("is_pool", POOL), ("pool_owner", POOL)])

    def test_a_throttled_or_failed_read_keeps_nothing(self):
        self.reader.error = Throttled("rate limited")
        self.assertEqual(self.refused(record(), 429, "upstream_busy")["retry_after_s"], 30)
        self.reader.error = RuntimeError("eth_call: boom")
        self.refused(record(), 424, "upstream_failed")
        # Neither was taken for an answer: once the node reads again, the same record is kept.
        self.reader.error = None
        self.assertEqual(self.contacts.write(record())[0], 200)

    def test_the_readers_reads(self):
        calls = []

        def rpc(method, params):
            calls.append(params[0])
            if params[0]["to"].lower() == "0x00000000000000000000000000000000000000f1":
                return "0x" + "00" * 31 + "01"
            return "0x" + "00" * 12 + OWNER.address[2:].lower()

        reader = JsonRpcReader("http://rpc.invalid", "0x00000000000000000000000000000000000000F1",
                               "0x00000000000000000000000000000000000000F2")
        reader._rpc = lambda method, params: rpc(method, params)
        self.assertTrue(reader.is_pool(POOL))
        self.assertEqual(reader.pool_owner(POOL), OWNER.address)
        self.assertEqual([c["data"][:10] for c in calls],
                         ["0x" + keccak(text="isPool(address)")[:4].hex(), "0x" + keccak(text="owner()")[:4].hex()])
        self.assertEqual(calls[1]["to"], POOL)


class TheBook(ContactsTest):
    def test_a_restart_reads_the_last_record_back(self):
        self.contacts.write(record(NOW_MS, **GIVEN))
        self.contacts.write(record(NOW_MS + 1, pool=OTHER_POOL, key=STRANGER, x="https://x.com/example"))
        self.contacts.write(record(NOW_MS + 2, email=EMAIL))
        again = Contacts(Reader(), Book(self.dir), clock=lambda: NOW)
        self.assertEqual(again.read(POOL)[1]["contacts"]["email"], EMAIL)
        self.assertEqual(again.read(POOL)[1]["contacts"]["discord"], "")
        self.assertEqual(again.read(OTHER_POOL)[1]["signer"], STRANGER.address)
        self.assertEqual(again.write(record(NOW_MS + 2))[1]["code"], "stale_nonce")

    def test_the_highest_nonce_is_the_last_whatever_the_order_of_lines(self):
        path = pathlib.Path(self.dir) / "contacts.jsonl"
        path.write_text(json.dumps({"pool": POOL, "nonce": 9, "contacts": {"x": "https://x.com/nine"}}) + "\n"
                        + json.dumps({"pool": POOL, "nonce": 3, "contacts": {"x": "https://x.com/three"}}) + "\n")
        self.assertEqual(Book(self.dir).last(POOL)["nonce"], 9)

    def test_a_line_cut_short_at_the_end_is_skipped_and_one_inside_stops_the_start(self):
        self.contacts.write(record(**GIVEN))
        path = pathlib.Path(self.dir) / "contacts.jsonl"
        good = path.read_text()
        path.write_text(good + '{"pool": "0x69e8')
        self.assertEqual(Book(self.dir).last(POOL)["contacts"], GIVEN)
        path.write_text('{"pool": "0x69e8\n' + good)
        with self.assertRaises(SystemExit) as stop:
            Book(self.dir)
        self.assertIn("line 1", str(stop.exception))


class FromEnv(unittest.TestCase):
    def test_contacts_are_off_without_a_directory(self):
        self.assertIsNone(contacts_from_env({}, Reader()))
        self.assertIsNone(contacts_directory({"STATE_DIRECTORY": "/var/lib/colosseum-gw/faucet-demo3"}))

    def test_the_directory_is_named_or_the_one_systemd_made_for_contacts(self):
        self.assertEqual(contacts_directory({"STATE_DIRECTORY": "/var/lib/colosseum-gw/faucet-demo3:"
                                                                 "/var/lib/colosseum-gw/contacts-demo3"}),
                         "/var/lib/colosseum-gw/contacts-demo3")
        self.assertEqual(contacts_directory({"GATEWAY_CONTACTS_DIR": "/srv/c", "STATE_DIRECTORY": "/x/contacts-a"}),
                         "/srv/c")
        directory = tempfile.mkdtemp()
        self.assertIsInstance(contacts_from_env({"GATEWAY_CONTACTS_DIR": directory}, Reader()), Contacts)

    def test_a_directory_it_cannot_write_stops_the_start(self):
        directory = tempfile.mkdtemp()
        os.chmod(directory, 0o500)
        self.addCleanup(os.chmod, directory, 0o700)
        for d in (directory, os.path.join(directory, "missing")):
            with self.subTest(d=d), self.assertRaises(SystemExit):
                contacts_from_env({"GATEWAY_CONTACTS_DIR": d}, Reader())


class OverHttp(unittest.TestCase):
    """The path on a real socket: the pool in the query, the record in the body, and a gateway without
    contacts says so."""

    def serve(self, contacts):
        from http.server import ThreadingHTTPServer

        from gateway.server import make_handler

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(object(), "", contacts=contacts))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def ask(self, port, method, path, body=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        out = resp.status, json.loads(resp.read() or b"null")
        conn.close()
        return out

    def test_a_record_is_kept_and_read_over_the_paths(self):
        port = self.serve(Contacts(Reader(), Book(tempfile.mkdtemp()), clock=lambda: NOW))
        self.assertEqual(self.ask(port, "GET", f"/v1/contacts?pool={POOL}")[1]["code"], "no_contacts")
        status, out = self.ask(port, "POST", "/v1/contacts", json.dumps(record(**GIVEN)))
        self.assertEqual((status, out["status"]), (200, "kept"))
        status, out = self.ask(port, "GET", f"/v1/contacts?pool={POOL.lower()}")
        self.assertEqual((status, out["contacts"]), (200, GIVEN))
        self.assertEqual(self.ask(port, "GET", "/v1/contacts")[1]["code"], "bad_pool")
        self.assertEqual(self.ask(port, "POST", "/v1/contacts", "{nope")[0], 400)

    def test_a_gateway_without_contacts(self):
        port = self.serve(None)
        for method, path, body in (("GET", f"/v1/contacts?pool={POOL}", None),
                                   ("POST", "/v1/contacts", json.dumps(record()))):
            with self.subTest(method=method):
                status, out = self.ask(port, method, path, body)
                self.assertEqual((status, out["code"]), (404, "no_contacts_here"))


if __name__ == "__main__":
    unittest.main()
