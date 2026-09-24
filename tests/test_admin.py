import re
import sqlite3
import tempfile
import time
import unittest

from afterword import security

from helpers import (BLOG, Client, FakeLaya, cleanup, make_app, post_comment,
                     set_password_and_login, use_fake_laya)

ADMIN_ORIGIN = "https://comments.example.com"


class SetupAndLoginTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = Client(self.app)

    def tearDown(self):
        cleanup(self.app)

    def test_everything_redirects_to_setup_before_password(self):
        for path in ("/admin", "/admin/comments", "/admin/settings", "/admin/login"):
            res = self.client.get(path)
            self.assertEqual((res.status, res.headers["location"]), (303, "/admin/setup"), path)

    def test_setup_with_code(self):
        code = self.app.new_setup_code()
        form = {"code": "WRONG-CODE-XXXX", "password": "long enough password", "confirm": "long enough password"}
        bad = self.client.post("/admin/setup", form=form, headers={"Origin": ADMIN_ORIGIN})
        self.assertIn("not right", bad.text)
        form["code"] = code.lower()
        ok = self.client.post("/admin/setup", form=form, headers={"Origin": ADMIN_ORIGIN})
        self.assertEqual(ok.status, 303)
        self.assertEqual(self.client.get("/admin/comments").status, 200)
        # The code is single-use and setup is closed afterwards.
        self.assertIsNone(self.app.db.meta_get("setup_code_hash"))
        self.assertEqual(self.client.get("/admin/setup").status, 303)

    def test_setup_rejects_short_password_and_expired_code(self):
        code = self.app.new_setup_code()
        res = self.client.post("/admin/setup", form={"code": code, "password": "short", "confirm": "short"},
                               headers={"Origin": ADMIN_ORIGIN})
        self.assertIn("at least 12", res.text)
        self.app.db.meta_set("setup_code_expires", str(int(time.time()) - 1))
        res = self.client.post("/admin/setup", form={"code": code, "password": "x" * 20, "confirm": "x" * 20},
                               headers={"Origin": ADMIN_ORIGIN})
        self.assertIn("expired", res.text)

    def test_session_cookie_is_hardened(self):
        self.app.db.meta_set("admin_password", security.hash_password("correct horse battery"))
        res = self.client.post("/admin/login", form={"password": "correct horse battery"},
                               headers={"Origin": ADMIN_ORIGIN})
        cookie = res.cookies()[0]
        self.assertTrue(cookie.startswith("__Host-afterword="))
        for attribute in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain", cookie)

    def test_wrong_password_and_lockout(self):
        self.app.db.meta_set("admin_password", security.hash_password("correct horse battery"))
        for _ in range(10):
            res = self.client.post("/admin/login", form={"password": "nope"}, headers={"Origin": ADMIN_ORIGIN})
            self.assertEqual(res.status, 401)
        res = self.client.post("/admin/login", form={"password": "correct horse battery"},
                               headers={"Origin": ADMIN_ORIGIN})
        self.assertIn("Too many attempts", res.text)
        other = Client(self.app, remote_addr="198.51.100.20")
        ok = other.post("/admin/login", form={"password": "correct horse battery"},
                        headers={"Origin": ADMIN_ORIGIN})
        self.assertEqual(ok.status, 303)

    def test_login_from_other_origin_refused(self):
        self.app.db.meta_set("admin_password", security.hash_password("correct horse battery"))
        res = self.client.post("/admin/login", form={"password": "correct horse battery"},
                               headers={"Origin": "https://evil.example"})
        self.assertEqual(res.status, 403)

    def test_forged_or_expired_session_cookie(self):
        self.app.db.meta_set("admin_password", security.hash_password("correct horse battery"))
        self.client.jar["__Host-afterword"] = "forged"
        self.assertEqual(self.client.get("/admin/comments").status, 303)
        set_password_and_login(self.app, self.client)
        conn = self.app.db.conn()
        with conn:
            conn.execute("UPDATE sessions SET expires_at = 0")
        self.assertEqual(self.client.get("/admin/comments").status, 303)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.reader = Client(self.app, remote_addr="198.51.100.4")
        self.admin = Client(self.app)
        set_password_and_login(self.app, self.admin)

    def tearDown(self):
        cleanup(self.app)

    def ids(self, status=None):
        sql = "SELECT public_id FROM comments" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        return [r[0] for r in self.app.db.conn().execute(sql, (status,) if status else ())]

    def test_security_headers(self):
        res = self.admin.get("/admin/comments")
        csp = res.headers["content-security-policy"]
        for directive in ("default-src 'none'", "script-src 'self'", "frame-ancestors 'none'",
                          "form-action 'self'", "base-uri 'none'"):
            self.assertIn(directive, csp)
        self.assertEqual(res.headers["x-frame-options"], "DENY")
        # "no-referrer" would make browsers send Origin: null on the dashboard's own forms.
        self.assertEqual(res.headers["referrer-policy"], "same-origin")
        self.assertEqual(res.headers["cache-control"], "no-store")
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotRegex(res.text, r"<script(?![^>]*src=)")  # no inline scripts
        self.assertNotRegex(res.text, r"\sstyle=")              # no inline styles
        self.assertNotRegex(res.text, r"\son[a-z]+=")           # no inline handlers

    def test_hostile_comment_is_escaped_everywhere(self):
        payload = '<script>alert(1)</script><img src=x onerror="alert(2)">'
        post_comment(self.reader, author='"><svg onload=alert(3)>', body=payload,
                     email='x"@example.com', thread='/p"><script>alert(4)</script>')
        html = self.admin.get("/admin/comments").text
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<svg", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        all_html = self.admin.get("/admin/comments", query={"status": "all", "q": "<script>"}).text
        self.assertNotIn("<script>alert", all_html)

    def test_post_without_csrf_rejected(self):
        post_comment(self.reader)
        pid = self.ids()[0]
        res = self.admin.post("/admin/comments", form={"one": f"approve:{pid}"}, headers={"Origin": ADMIN_ORIGIN})
        self.assertEqual(res.status, 403)
        res = self.admin.post("/admin/comments", form={"one": f"approve:{pid}", "csrf": "guess"},
                              headers={"Origin": ADMIN_ORIGIN})
        self.assertEqual(res.status, 403)
        self.assertEqual(self.ids("approved"), [])

    def test_cross_site_post_rejected_even_with_token(self):
        post_comment(self.reader)
        pid = self.ids()[0]
        token = self.admin.csrf()
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Referer": "https://evil.example/page"}, {"Origin": "null"}):
            with self.subTest(headers=headers):
                res = self.admin.post("/admin/comments", form={"one": f"approve:{pid}", "csrf": token},
                                      headers=headers)
                self.assertEqual(res.status, 403)
        self.assertEqual(self.ids("approved"), [])

    def test_approve_reject_pending_delete(self):
        for i in range(3):
            post_comment(self.reader, body=f"Comment {i}")
        a, b, c = self.ids()
        self.admin.admin_post("/admin/comments", {"one": f"approve:{a}"})
        self.admin.admin_post("/admin/comments", {"action": "reject", "id": [b, c], "bulk": "1"})
        self.assertEqual(self.ids("approved"), [a])
        self.assertEqual(self.ids("rejected"), [b, c])
        row = self.app.db.conn().execute("SELECT decided_by, reason FROM comments WHERE public_id=?",
                                         (a,)).fetchone()
        self.assertEqual((row[0], row[1]), ("admin", "Published by you."))
        self.admin.admin_post("/admin/comments", {"one": f"pending:{b}"})
        self.admin.admin_post("/admin/comments", {"action": "delete", "id": [c], "bulk": "1"})
        self.assertEqual(self.ids(), [a, b])
        public = self.reader.get("/api/v1/thread", query={"id": "/posts/hello"}, headers={"Origin": BLOG}).json()
        self.assertEqual([x["id"] for x in public["comments"]], [a])

    def test_action_ids_are_sanitised(self):
        post_comment(self.reader)
        res = self.admin.admin_post("/admin/comments", {"action": "delete", "id": ["x' OR '1'='1"], "bulk": "1"})
        self.assertEqual(res.status, 303)
        self.assertEqual(len(self.ids()), 1)

    def test_back_parameter_cannot_redirect_offsite(self):
        post_comment(self.reader)
        pid = self.ids()[0]
        res = self.admin.admin_post("/admin/comments", {"one": f"approve:{pid}",
                                                        "back": "status=//evil.example&q=x"})
        self.assertTrue(res.headers["location"].startswith("/admin/comments"))

    def test_filters_and_search(self):
        post_comment(self.reader, body="apples", thread="/one")
        post_comment(self.reader, body="pears", thread="/two", author="Grace")
        html = self.admin.get("/admin/comments", query={"status": "all", "q": "pear"}).text
        self.assertIn("pears", html)
        self.assertNotIn("apples", html)
        html = self.admin.get("/admin/comments", query={"status": "all", "thread": "/one"}).text
        self.assertIn("apples", html)
        self.assertNotIn("pears", html)
        # LIKE wildcards are literal
        html = self.admin.get("/admin/comments", query={"status": "all", "q": "%"}).text
        self.assertIn("No comments match", html)

    def test_settings_validation(self):
        res = self.admin.admin_post("/admin/settings", {"site_origins": "javascript:alert(1)",
                                                        "moderation_mode": "manual",
                                                        "automatic_decider": "basic", "formatting": "basic",
                                                        "thread_order": "oldest", "max_body_chars": "5000",
                                                        "min_seconds": "3", "max_links": "2",
                                                        "rate_per_ip": "5", "rate_global": "60",
                                                        "purge_rejected_days": "30"},
                                    headers={"Origin": ADMIN_ORIGIN})
        self.assertEqual(res.status, 200)
        self.assertIn("is not a site address", res.text)
        self.assertEqual(self.app.settings.get("site_origins"), [BLOG])

    def test_settings_save_and_normalise_origins(self):
        form = {"site_origins": "HTTPS://Blog.Example.com/\nhttp://localhost:3000",
                "comments_open": "1", "moderation_mode": "automatic", "automatic_decider": "basic",
                "formatting": "plain", "thread_order": "newest", "max_body_chars": "3000",
                "min_seconds": "2", "max_links": "1", "rate_per_ip": "4", "rate_global": "40",
                "purge_rejected_days": "7", "blocked_terms": "viagra"}
        res = self.admin.admin_post("/admin/settings", form)
        self.assertEqual(res.status, 303)
        s = self.app.settings.all()
        self.assertEqual(s["site_origins"], ["https://blog.example.com", "http://localhost:3000"])
        self.assertEqual((s["moderation_mode"], s["formatting"], s["linkify"]), ("automatic", "plain", False))

    def test_laya_threshold_cross_check(self):
        res = self.admin.admin_post("/admin/laya", {
            "laya_source": "external", "laya_url": "http://127.0.0.1:8000", "laya_model": "auto",
            "laya_timeout": "3", "laya_flag_at": "0.5", "laya_approve_below": "0.95",
            "laya_reject_at": "0.9", "laya_fallback": "hold", "laya_question": "q?",
            "laya_genuine": "g", "laya_spam": "s"})
        self.assertIn("must not be higher", res.text)

    def test_laya_page_test_box_and_rescore(self):
        fake = FakeLaya()
        try:
            use_fake_laya(self.app, fake)
            res = self.admin.admin_post("/admin/laya/test", {"test_author": "x", "test_body": "casino bonus"})
            self.assertIn("0.97", res.text)
            self.assertIn("would be rejected", res.text)
            self.app.settings.update({"moderation_mode": "manual"})
            post_comment(self.reader, body="casino")
            pid = self.ids()[0]
            self.admin.admin_post("/admin/comments", {"one": f"rescore:{pid}"})
            row = self.app.db.conn().execute("SELECT status, laya_score FROM comments").fetchone()
            self.assertEqual(row[0], "pending")          # rescoring never changes status
            self.assertAlmostEqual(row[1], 0.97)
            # The API key is never echoed back into the page.
            self.assertNotIn("test-key", self.admin.get("/admin/laya").text)
        finally:
            fake.close()

    def test_agreement_table(self):
        fake = FakeLaya()
        try:
            self.app.settings.update({"moderation_mode": "assisted"})
            use_fake_laya(self.app, fake)
            post_comment(self.reader, body="casino time")
            post_comment(self.reader, body="Good point about fonts")
            spam, good = self.ids()
            self.admin.admin_post("/admin/comments", {"one": f"reject:{spam}"})
            self.admin.admin_post("/admin/comments", {"one": f"approve:{good}"})
            html = self.admin.get("/admin/laya").text
            self.assertIn('<table class="agreement">', html)
        finally:
            fake.close()

    def test_backup_download_is_valid_sqlite(self):
        post_comment(self.reader)
        res = self.admin.get("/admin/backup")
        self.assertEqual(res.status, 200)
        self.assertIn("attachment", res.headers["content-disposition"])
        with tempfile.NamedTemporaryFile(suffix=".db") as fh:
            fh.write(res.body)
            fh.flush()
            n = sqlite3.connect(fh.name).execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        self.assertEqual(n, 1)

    def test_backup_requires_login(self):
        self.assertEqual(Client(self.app).get("/admin/backup").status, 303)

    def test_password_change_signs_out_other_sessions(self):
        other = Client(self.app, remote_addr="198.51.100.30")
        set_password_and_login(self.app, other)
        res = self.admin.admin_post("/admin/account", {"do": "password", "current": "correct horse battery",
                                                       "password": "an even longer passphrase",
                                                       "confirm": "an even longer passphrase"})
        self.assertEqual(res.status, 303)
        self.assertEqual(self.admin.get("/admin/account").status, 200)
        self.assertEqual(other.get("/admin/account").status, 303)

    def test_logout(self):
        self.admin.admin_post("/admin/logout", {})
        self.assertEqual(self.admin.get("/admin/comments").status, 303)

    def test_embed_page_uses_real_widget_hash(self):
        import base64
        import hashlib
        html = self.admin.get("/admin/embed").text
        expected = base64.b64encode(hashlib.sha384(self.app.static["/widget.js"][0]).digest()).decode()
        self.assertIn(expected, html)
        self.assertIn("data-thread=&quot;{{.ID}}&quot;", html)   # shown escaped, as text
        self.assertIn("https://comments.example.com/widget.js", html)

    def test_maintenance_purges_and_forgets(self):
        post_comment(self.reader)
        pid = self.ids()[0]
        self.admin.admin_post("/admin/comments", {"one": f"reject:{pid}"})
        self.app.maintenance(now=time.time() + 31 * 86400)
        self.assertEqual(self.ids(), [])
        post_comment(self.reader, body="another")
        self.app.maintenance(now=time.time() + 31 * 86400)
        self.assertIsNone(self.app.db.conn().execute("SELECT ip_key FROM comments").fetchone()[0])

    def test_all_pages_render(self):
        for path in ("/admin/comments", "/admin/settings", "/admin/laya", "/admin/embed", "/admin/account"):
            with self.subTest(path=path):
                res = self.admin.get(path)
                self.assertEqual(res.status, 200)
                self.assertTrue(re.search(r"<h1>", res.text))


if __name__ == "__main__":
    unittest.main()
