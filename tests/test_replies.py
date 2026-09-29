import os
import sqlite3
import tempfile
import time
import unittest

from afterword.db import MIGRATIONS, Database

from helpers import BLOG, Client, cleanup, make_app, post_comment, set_password_and_login


def thread(client, thread_id="/posts/hello"):
    return client.get("/api/v1/thread", query={"id": thread_id}, headers={"Origin": BLOG}).json()


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "basic"})
        self.reader = Client(self.app)

    def tearDown(self):
        cleanup(self.app)

    def post(self, author="Ada", body="A comment", reply_to=None, **kw):
        extra = {"reply_to": reply_to} if reply_to is not None else {}
        return post_comment(self.reader, author=author, body=body, extra=extra, **kw)

    def pid(self, body):
        return self.app.db.conn().execute("SELECT public_id FROM comments WHERE body = ?", (body,)).fetchone()[0]

    def set_status(self, body, status):
        with self.app.db.conn() as conn:
            conn.execute("UPDATE comments SET status = ? WHERE body = ?", (status, body))

    def test_reply_is_shown_under_its_comment_with_a_reference(self):
        top = self.post(author="Ada", body="Top").json()["comment"]
        res = self.post(author="Grace", body="Answer", reply_to=top["id"])
        self.assertEqual(res.status, 201, res.text)
        reply = res.json()["comment"]
        self.assertEqual(reply["parent"], top["id"])
        self.assertEqual(reply["reply_to"], {"id": top["id"], "author": "Ada", "created": top["created"]})
        data = thread(self.reader)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["comments"]), 1)
        shown = data["comments"][0]
        self.assertEqual((shown["id"], shown["parent"], shown["reply_to"]), (top["id"], None, None))
        self.assertEqual([r["text"] for r in shown["replies"]], ["Answer"])
        self.assertNotIn("replies", shown["replies"][0])      # one level only
        self.assertTrue(data["form"]["replies"])

    def test_reply_to_a_reply_joins_the_same_comment_and_names_whom_it_answers(self):
        top = self.post(author="Ada", body="Top").json()["comment"]
        first = self.post(author="Grace", body="First answer", reply_to=top["id"]).json()["comment"]
        second = self.post(author="Ada", body="Answer to Grace", reply_to=first["id"]).json()["comment"]
        self.assertEqual(second["parent"], top["id"])
        self.assertEqual(second["reply_to"]["author"], "Grace")
        self.assertEqual(second["reply_to"]["id"], first["id"])
        data = thread(self.reader)
        self.assertEqual(len(data["comments"]), 1)
        self.assertEqual([(r["text"], r["reply_to"]["author"]) for r in data["comments"][0]["replies"]],
                         [("First answer", "Ada"), ("Answer to Grace", "Grace")])
        parent_ids = {r[0] for r in self.app.db.conn().execute(
            "SELECT parent_id FROM comments WHERE parent_id IS NOT NULL")}
        self.assertEqual(len(parent_ids), 1)

    def test_newest_first_orders_comments_but_replies_read_as_a_conversation(self):
        self.app.settings.update({"thread_order": "newest"})
        old = self.post(body="Old").json()["comment"]
        self.post(body="New")
        self.post(body="Reply one", reply_to=old["id"])
        self.post(body="Reply two", reply_to=old["id"])
        data = thread(self.reader)
        self.assertEqual([c["text"] for c in data["comments"]], ["New", "Old"])
        self.assertEqual([r["text"] for r in data["comments"][1]["replies"]], ["Reply one", "Reply two"])

    def test_only_shown_comments_in_the_same_thread_can_be_answered(self):
        self.post(body="Elsewhere", thread="/posts/other")
        self.app.settings.update({"moderation_mode": "manual"})
        self.post(body="Held")
        self.app.settings.update({"moderation_mode": "automatic"})
        for target in (self.pid("Elsewhere"), self.pid("Held"), "doesnotexist", "../x", "a" * 40, ["x"], 7):
            with self.subTest(target=target):
                res = self.post(body="Reply", reply_to=target)
                self.assertEqual((res.status, res.json()["error"]), (400, "reply_unavailable"))
        self.assertEqual(self.app.db.conn().execute(
            "SELECT COUNT(*) FROM comments WHERE body = 'Reply'").fetchone()[0], 0)

    def test_replies_can_be_turned_off(self):
        top = self.post(body="Top").json()["comment"]
        self.post(body="Earlier reply", reply_to=top["id"])
        self.app.settings.update({"replies": False})
        res = self.post(body="Late reply", reply_to=top["id"])
        self.assertEqual((res.status, res.json()["error"]), (403, "replies_off"))
        data = thread(self.reader)
        self.assertFalse(data["form"]["replies"])
        self.assertEqual([r["text"] for r in data["comments"][0]["replies"]], ["Earlier reply"])

    def test_reply_stands_alone_when_its_comment_is_not_shown(self):
        top = self.post(author="Ada", body="Top").json()["comment"]
        self.post(author="Grace", body="Answer", reply_to=top["id"])
        self.set_status("Top", "rejected")
        data = thread(self.reader)
        self.assertEqual([c["text"] for c in data["comments"]], ["Answer"])
        orphan = data["comments"][0]
        self.assertEqual(orphan["reply_to"], {"id": None, "author": "Ada", "created": top["created"]})
        self.assertIsNone(orphan["parent"])
        # Deleting it for good keeps the reply and its reference.
        with self.app.db.conn() as conn:
            conn.execute("DELETE FROM comments WHERE body = 'Top'")
        self.assertIsNone(self.app.db.conn().execute(
            "SELECT parent_id FROM comments WHERE body = 'Answer'").fetchone()[0])
        self.assertEqual(thread(self.reader)["comments"][0]["reply_to"]["author"], "Ada")

    def test_display_stays_one_level_deep_whatever_happened_in_between(self):
        top = self.post(body="Top").json()["comment"]
        first = self.post(body="First", reply_to=top["id"]).json()["comment"]
        self.set_status("Top", "pending")
        # "First" is shown on its own now, so answering it files the reply under it...
        self.post(body="Second", reply_to=first["id"])
        # ...and once "Top" is back, everything is again one conversation, one level deep.
        self.set_status("Top", "approved")
        data = thread(self.reader)
        self.assertEqual([c["text"] for c in data["comments"]], ["Top"])
        self.assertEqual([r["text"] for r in data["comments"][0]["replies"]], ["First", "Second"])

    def test_dashboard_shows_whom_a_reply_answers(self):
        admin = Client(self.app, remote_addr="192.0.2.10")
        set_password_and_login(self.app, admin)
        top = self.post(author="Ada <b>", body="Top").json()["comment"]
        self.post(author="Grace", body="Answer", reply_to=top["id"])
        html = admin.get("/admin/comments", query={"status": "all"}).text
        self.assertIn('<p class="comment-reply">Reply to <strong>@Ada &lt;b&gt;</strong>', html)
        self.assertEqual(html.count('class="comment-reply"'), 1)
        settings = admin.get("/admin/settings").text
        self.assertIn('id="replies" name="replies" value="1" checked', settings)


class ReplyMigrationTests(unittest.TestCase):
    def test_upgrade_from_1_1_keeps_every_comment_top_level(self):
        directory = tempfile.mkdtemp(prefix="afterword-migrate-")
        path = os.path.join(directory, "afterword.db")
        old = sqlite3.connect(path)
        for version, script in enumerate(MIGRATIONS[:2], start=1):
            old.executescript("BEGIN;" + script + f";\nPRAGMA user_version = {version};\nCOMMIT;")
        old.execute("INSERT INTO comments (public_id, thread, author, body, body_hash, status, created_at) "
                    "VALUES ('old1', '/posts/hello', 'Ada', 'Written in 1.1', 'h', 'approved', ?)",
                    (int(time.time()) - 60,))
        old.commit()
        old.close()
        Database(path)
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT parent_id, reply_to FROM comments").fetchall(), [(None, None)])
        conn.close()
        app = make_app(data_dir=directory)
        try:
            app.settings.update({"moderation_mode": "automatic"})
            reader = Client(app)
            res = post_comment(reader, author="Grace", body="Late reply", extra={"reply_to": "old1"})
            self.assertEqual(res.status, 201, res.text)
            data = thread(reader)
            self.assertEqual([(c["text"], [r["text"] for r in c["replies"]]) for c in data["comments"]],
                             [("Written in 1.1", ["Late reply"])])
        finally:
            cleanup(app)


if __name__ == "__main__":
    unittest.main()
