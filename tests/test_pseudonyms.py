import os
import re
import sqlite3
import tempfile
import time
import unittest

from afterword import pseudonyms
from afterword.db import MIGRATIONS, Database

from helpers import (BLOG, Client, FakeLaya, cleanup, make_app, post_comment,
                     set_password_and_login, use_fake_laya)

KEY_A = "abcdefghijklmnopqrstuvwxyz234567"
KEY_B = "b" * 32


def thread(client, thread_id="/posts/hello"):
    return client.get("/api/v1/thread", query={"id": thread_id}, headers={"Origin": BLOG}).json()


def restore(client, key, origin=BLOG):
    return client.post("/api/v1/pseudonym", json_body={"key": key},
                       headers={"Origin": origin} if origin else {})


class NameKeyTests(unittest.TestCase):
    def test_lookalikes_share_a_key(self):
        same = ["Mara", "mara", "MARA", " Mara! ", "Mára", "Mаra", "Ｍａｒａ",
                "M a r a", "Mara ✓", "Mara🦊", "M.A.R.A", "\U0001d40c\U0001d41a\U0001d42b\U0001d41a"]
        for name in same:
            with self.subTest(name=name):
                self.assertEqual(pseudonyms.name_key(name), "mara")
        self.assertEqual(pseudonyms.name_key("Iris"), pseudonyms.name_key("lris"))
        self.assertEqual(pseudonyms.name_key("Iris"), pseudonyms.name_key("1ris"))
        self.assertEqual(pseudonyms.name_key("Arnold"), pseudonyms.name_key("Amold"))
        self.assertEqual(pseudonyms.name_key("B0B"), pseudonyms.name_key("Bob"))
        self.assertEqual(pseudonyms.name_key("ВОВ"), pseudonyms.name_key("BOB"))  # Cyrillic

    def test_different_names_stay_different(self):
        for a, b in (("Mara", "Maria"), ("Ada", "Adam"), ("Grace", "Gracie"), ("李明", "李朋")):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(pseudonyms.name_key(a), pseudonyms.name_key(b))
        self.assertEqual(pseudonyms.name_key("李明"), "李明")
        self.assertEqual(pseudonyms.name_key("🦊 ✨"), "")

    def test_key_format(self):
        self.assertEqual(pseudonyms.normalize_key(KEY_A), KEY_A)
        self.assertEqual(pseudonyms.normalize_key("ABCD-efgh-ijkl-mnop-qrst-uvwx-yz23-4567"), KEY_A)
        for bad in (None, 5, [], "", "a" * 31, "a" * 33, "1" * 32, "abcd" * 7 + "abc!", "a" * 500):
            self.assertIsNone(pseudonyms.normalize_key(bad), bad)

    def test_key_hash_depends_on_server_secret(self):
        self.assertNotEqual(pseudonyms.key_hash(b"x" * 32, KEY_A), pseudonyms.key_hash(b"y" * 32, KEY_A))
        self.assertNotIn(KEY_A, pseudonyms.key_hash(b"x" * 32, KEY_A))


class PseudonymApiTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "basic",
                                  "pseudonyms": True})
        self.reader = Client(self.app)
        self.other = Client(self.app, remote_addr="198.51.100.44")

    def tearDown(self):
        cleanup(self.app)

    def db(self):
        return self.app.db.conn()

    def claim(self, client=None, author="Mara", key=KEY_A, **kw):
        return post_comment(client or self.reader, author=author, extra={"key": key}, **kw)

    # -- claiming and using -----------------------------------------------------------------
    def test_first_comment_claims_and_later_ones_are_verified(self):
        first = self.claim(body="First")
        self.assertEqual(first.status, 201, first.text)
        self.assertEqual(first.json()["pseudonym"], {"name": "Mara"})
        self.assertIs(first.json()["comment"]["verified"], True)
        # From another network and with the key typed in dashed form: still the same holder.
        second = self.claim(self.other, key="ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567", body="Second")
        self.assertEqual(second.status, 201, second.text)
        data = thread(self.reader)
        self.assertTrue(data["pseudonyms"])
        self.assertEqual([c["verified"] for c in data["comments"]], [True, True])
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 1)

    def test_other_readers_cannot_post_as_the_pseudonym(self):
        self.claim()
        for name in ("Mara", "mara", "MARA", "Mára", "Mаra", "Mara ", "M-a-r-a"):
            with self.subTest(name=name):
                res = post_comment(self.other, author=name, body=f"I am {name}")
                self.assertEqual(res.status, 409)
                self.assertEqual(res.json()["error"], "name_reserved")
                self.assertEqual(res.json()["field"], "author")
        taken = self.claim(self.other, author="MARA", key=KEY_B, body="Mine now")
        self.assertEqual((taken.status, taken.json()["error"]), (409, "pseudonym_taken"))
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comments").fetchone()[0], 1)
        # Unrelated names are still free for anyone, and shown as unverified.
        free = post_comment(self.other, author="Maria", body="Different person")
        self.assertEqual(free.status, 201)
        self.assertIs(free.json()["comment"]["verified"], False)
        self.assertNotIn("pseudonym", free.json())

    def test_key_only_works_with_its_own_name(self):
        self.claim()
        res = self.claim(author="Someone Else", body="Hello")
        self.assertEqual((res.status, res.json()["error"]), (400, "pseudonym_mismatch"))
        self.assertIn("Mara", res.json()["message"])

    def test_invalid_keys_and_names(self):
        for key in ("short", "!" * 32, 12345, ["x"], {"k": 1}):
            with self.subTest(key=key):
                res = self.claim(key=key)
                self.assertEqual((res.status, res.json()["error"]), (400, "pseudonym_key_invalid"))
        res = self.claim(author="🦊✨")
        self.assertEqual((res.status, res.json()["error"]), (400, "pseudonym_name_invalid"))
        for name in ("Mara ✓", "✔ Bob", "Ann ✅", "Mara verified", "VERIFIED Joe",
                     "Bob (v e r i f i e d)", "Unverified Ann"):
            with self.subTest(name=name):
                res = post_comment(self.reader, author=name)
                self.assertEqual((res.status, res.json()["error"]), (400, "name_marker"))
        self.assertEqual(post_comment(self.reader, author="Vera Fied").status, 201)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 0)

    def test_failed_submission_claims_nothing(self):
        for extra in ({"body": ""}, {"token": "bad"}, {"thread": ""}):
            payload = {"key": KEY_A}
            payload.update(extra)
            post_comment(self.reader, author="Mara", extra=payload)
        post_comment(self.reader, author="Mara", extra={"key": KEY_A, "website": "http://spam"})
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 0)
        self.assertEqual(post_comment(self.other, author="Mara").status, 201)

    def test_claim_while_held_for_review(self):
        self.app.settings.update({"moderation_mode": "manual"})
        res = self.claim()
        self.assertEqual(res.status, 202)
        self.assertEqual(res.json(), {"status": "pending", "token": res.json()["token"],
                                      "pseudonym": {"name": "Mara"}})
        self.assertEqual(post_comment(self.other, author="Mara").json()["error"], "name_reserved")

    def test_claim_with_laya_deciding(self):
        fake = FakeLaya()
        try:
            self.app.settings.update({"automatic_decider": "laya"})
            use_fake_laya(self.app, fake)
            res = self.claim(body="Thoughtful reply")
            self.assertEqual(res.status, 201, res.text)
            self.assertTrue(res.json()["comment"]["verified"])
            spam = self.claim(author="Casino King", key=KEY_B, body="casino bonus")
            self.assertEqual(spam.json()["status"], "pending")   # same answer as held: spammers learn nothing
            rows = self.db().execute("SELECT author, status, pseudonym_id IS NOT NULL FROM comments "
                                     "ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [("Mara", "approved", 1), ("Casino King", "rejected", 1)])
        finally:
            fake.close()

    def test_older_comments_are_never_marked(self):
        post_comment(self.reader, author="Mara", body="Before claiming")
        self.claim(body="After claiming")
        data = thread(self.reader)
        self.assertEqual([(c["text"], c["verified"]) for c in data["comments"]],
                         [("Before claiming", False), ("After claiming", True)])

    def test_key_is_never_stored_or_returned(self):
        self.claim()
        restore(self.reader, KEY_A)
        self.app.db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for path in (self.app.cfg.db_path, self.app.cfg.db_path + "-wal"):
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
                self.assertNotIn(KEY_A.encode(), data)
                self.assertNotIn(KEY_A.upper().encode(), data)
        raw = self.reader.get("/api/v1/thread", query={"id": "/posts/hello"}, headers={"Origin": BLOG}).text
        self.assertNotIn(KEY_A, raw)
        self.assertNotIn("pseudonym_id", raw)
        stored = self.db().execute("SELECT key_hash FROM pseudonyms").fetchone()[0]
        self.assertNotIn(stored, raw)

    def test_same_key_twice_at_once_is_one_pseudonym(self):
        conn = self.db()
        first = pseudonyms.resolve(conn, self.app.secret, "Mara", KEY_A)
        again = pseudonyms.resolve(conn, self.app.secret, "Mara", KEY_A)
        rival = pseudonyms.resolve(conn, self.app.secret, "mara", KEY_B)
        with conn:
            pid = pseudonyms.attach(conn, first, int(time.time()))
        with conn:
            self.assertEqual(pseudonyms.attach(conn, again, int(time.time())), pid)
        with self.assertRaises(pseudonyms.Problem) as caught:
            with conn:
                pseudonyms.attach(conn, rival, int(time.time()))
        self.assertEqual(caught.exception.code, "pseudonym_taken")

    # -- turning it off -------------------------------------------------------------------
    def test_off_means_the_previous_behaviour(self):
        self.claim(body="Verified while on")
        self.app.settings.update({"pseudonyms": False})
        res = self.claim(body="Key sent while off")
        self.assertEqual((res.status, res.json()["error"]), (403, "pseudonyms_off"))
        self.assertEqual(post_comment(self.other, author="Mara", body="Anyone").status, 201)
        self.assertEqual(post_comment(self.other, author="Bob ✓", body="Marks allowed").status, 201)
        data = thread(self.reader)
        self.assertFalse(data["pseudonyms"])
        # What was verified stays verified; nothing else is.
        self.assertEqual([c["verified"] for c in data["comments"]], [True, False, False])
        self.assertEqual(restore(self.reader, KEY_A).json()["error"], "pseudonyms_off")

    def test_default_is_off(self):
        fresh = make_app()
        try:
            self.assertFalse(fresh.settings.get("pseudonyms"))
            self.assertFalse(thread(Client(fresh))["pseudonyms"])
        finally:
            cleanup(fresh)

    # -- restoring on another device ------------------------------------------------------
    def test_restore(self):
        self.claim()
        ok = restore(self.other, "abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567")
        self.assertEqual((ok.status, ok.json()), (200, {"pseudonym": {"name": "Mara"}}))
        self.assertEqual(ok.headers["access-control-allow-origin"], BLOG)
        self.assertEqual(restore(self.other, KEY_B).json()["error"], "pseudonym_unknown")
        self.assertEqual(restore(self.other, "nope").json()["error"], "pseudonym_key_invalid")
        self.assertEqual(restore(self.other, KEY_A, origin="https://evil.example").status, 403)
        preflight = self.reader.request("OPTIONS", "/api/v1/pseudonym", headers={"Origin": BLOG})
        self.assertEqual(preflight.status, 204)

    def test_restore_is_rate_limited(self):
        for _ in range(10):
            self.assertEqual(restore(self.other, KEY_B).status, 404)
        res = restore(self.other, KEY_B)
        self.assertEqual(res.status, 429)
        self.assertIn("retry-after", res.headers)
        self.assertEqual(restore(self.reader, KEY_B).status, 404)   # other senders are unaffected

    # -- names are released with their last comment --------------------------------------
    def test_names_without_comments_are_released(self):
        self.claim()
        pid = self.db().execute("SELECT public_id FROM comments").fetchone()[0]
        with self.db():
            self.db().execute("UPDATE comments SET status = 'rejected', decided_at = ? WHERE public_id = ?",
                              (int(time.time()), pid))
        self.app.maintenance()
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 1)
        self.app.maintenance(now=time.time() + 31 * 86400)   # rejected comment purged
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 0)
        self.assertEqual(post_comment(self.other, author="Mara").status, 201)


class MigrationTests(unittest.TestCase):
    def test_upgrade_from_1_0_keeps_comments_unverified(self):
        directory = tempfile.mkdtemp(prefix="afterword-migrate-")
        path = os.path.join(directory, "afterword.db")
        old = sqlite3.connect(path)
        old.executescript("BEGIN;" + MIGRATIONS[0] + ";\nPRAGMA user_version = 1;\nCOMMIT;")
        old.execute("INSERT INTO comments (public_id, thread, author, body, body_hash, status, created_at) "
                    "VALUES ('old1', '/posts/hello', 'Mara', 'Written in 1.0', 'h', 'approved', 1000)")
        old.commit()
        old.close()

        Database(path)          # what every start of the new version does
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
        self.assertEqual(conn.execute("SELECT author, pseudonym_id FROM comments").fetchall(), [("Mara", None)])
        conn.close()

        app = make_app(data_dir=directory)
        try:
            app.settings.update({"pseudonyms": True})
            reader = Client(app)
            data = thread(reader)
            self.assertEqual([(c["author"], c["verified"]) for c in data["comments"]], [("Mara", False)])
            # Claiming the name now does not reach back to the old comment.
            self.assertEqual(post_comment(reader, author="Mara", extra={"key": KEY_A}).status, 202)
            app.db.conn().execute("UPDATE comments SET status = 'approved'")
            app.db.conn().commit()
            self.assertEqual([c["verified"] for c in thread(reader)["comments"]], [False, True])
        finally:
            cleanup(app)

    def test_migration_is_idempotent(self):
        directory = tempfile.mkdtemp(prefix="afterword-migrate-")
        path = os.path.join(directory, "afterword.db")
        Database(path)
        Database(path)
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
        conn.close()


class PseudonymDashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "basic",
                                  "pseudonyms": True})
        self.admin = Client(self.app, remote_addr="192.0.2.10")
        set_password_and_login(self.app, self.admin)
        self.reader = Client(self.app)

    def tearDown(self):
        cleanup(self.app)

    def test_tag_filter_and_page(self):
        post_comment(self.reader, author="Mara", body="Unverified before", extra={})
        post_comment(self.reader, author="Mara", body="Verified one", extra={"key": KEY_A})
        html = self.admin.get("/admin/comments", query={"status": "all"}).text
        self.assertEqual(html.count("tag-pseudonym"), 1)
        pid = self.app.db.conn().execute("SELECT id FROM pseudonyms").fetchone()[0]
        html = self.admin.get("/admin/comments", query={"status": "all", "pseudonym": str(pid)}).text
        self.assertIn("Verified one", html)
        self.assertNotIn("Unverified before", html)
        self.assertIn("Showing only pseudonym <strong>Mara</strong>", html)
        # Odd filter values are ignored rather than crashing.
        self.assertEqual(self.admin.get("/admin/comments", query={"pseudonym": "²"}).status, 200)
        page = self.admin.get("/admin/pseudonyms").text
        self.assertIn(">Mara</a>", page)
        self.assertIn('href="/admin/pseudonyms"', page)          # in the navigation
        self.assertNotIn(KEY_A, page)
        self.assertNotIn(self.app.db.conn().execute("SELECT key_hash FROM pseudonyms").fetchone()[0], page)

    def test_release(self):
        post_comment(self.reader, author="Mara", body="By the first holder", extra={"key": KEY_A})
        pid = self.app.db.conn().execute("SELECT id FROM pseudonyms").fetchone()[0]
        res = self.admin.admin_post("/admin/pseudonyms", {"release": str(pid)})
        self.assertEqual(res.status, 303)
        self.assertEqual(self.app.db.conn().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 0)
        # Someone else can now claim the name, and does not inherit the first holder's comments.
        other = Client(self.app, remote_addr="198.51.100.9")
        self.assertEqual(post_comment(other, author="Mara", body="New holder", extra={"key": KEY_B}).status, 201)
        data = thread(self.reader)
        self.assertEqual([(c["text"], c["verified"]) for c in data["comments"]],
                         [("By the first holder", False), ("New holder", True)])
        # Releasing again, or garbage, is harmless.
        for value in (str(pid), "abc", "²", ""):
            self.assertEqual(self.admin.admin_post("/admin/pseudonyms", {"release": value}).status, 303)

    def test_release_needs_csrf(self):
        post_comment(self.reader, author="Mara", extra={"key": KEY_A})
        pid = self.app.db.conn().execute("SELECT id FROM pseudonyms").fetchone()[0]
        res = self.admin.post("/admin/pseudonyms", form={"release": str(pid)},
                              headers={"Origin": "https://comments.example.com"})
        self.assertEqual(res.status, 403)
        self.assertEqual(self.app.db.conn().execute("SELECT COUNT(*) FROM pseudonyms").fetchone()[0], 1)

    def test_setting_and_nav(self):
        self.app.settings.update({"pseudonyms": False})
        html = self.admin.get("/admin/comments").text
        self.assertNotIn('href="/admin/pseudonyms"', html)       # hidden while unused
        self.assertIn("Pseudonyms are off", self.admin.get("/admin/pseudonyms").text)
        form = self.admin.get("/admin/settings").text
        self.assertTrue(re.search(r'<input type="checkbox" id="pseudonyms" name="pseudonyms" value="1"', form))


if __name__ == "__main__":
    unittest.main()
