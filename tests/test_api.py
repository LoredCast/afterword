import json
import time
import unittest

from helpers import BLOG, Client, cleanup, make_app, post_comment, thread_token


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = Client(self.app)
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "basic"})

    def tearDown(self):
        cleanup(self.app)

    # -- CORS and origin ------------------------------------------------------------
    def test_thread_cors_only_for_allowed_origin(self):
        ok = self.client.get("/api/v1/thread", query={"id": "/a"}, headers={"Origin": BLOG})
        self.assertEqual(ok.headers["access-control-allow-origin"], BLOG)
        self.assertIn("Origin", ok.headers["vary"])
        other = self.client.get("/api/v1/thread", query={"id": "/a"},
                                headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", other.headers)

    def test_preflight(self):
        ok = self.client.request("OPTIONS", "/api/v1/comments", headers={"Origin": BLOG})
        self.assertEqual(ok.status, 204)
        self.assertEqual(ok.headers["access-control-allow-headers"], "Content-Type")
        self.assertNotIn("access-control-allow-credentials", ok.headers)
        bad = self.client.request("OPTIONS", "/api/v1/comments", headers={"Origin": "https://evil.example"})
        self.assertEqual(bad.status, 403)

    def test_post_requires_allowed_origin(self):
        for origin in ("https://evil.example", "", "null", "https://blog.example.com.evil.example"):
            with self.subTest(origin=origin):
                res = post_comment(self.client, origin=origin)
                self.assertEqual(res.status, 403)
                self.assertEqual(res.json()["error"], "origin_not_allowed")

    def test_post_requires_json_content_type(self):
        token = thread_token(self.client)
        res = self.client.post("/api/v1/comments", raw=json.dumps({"token": token}).encode(),
                               content_type="text/plain", headers={"Origin": BLOG})
        self.assertEqual(res.status, 415)

    def test_oversized_request_rejected(self):
        res = self.client.post("/api/v1/comments", raw=b"x" * (70 * 1024),
                               content_type="application/json", headers={"Origin": BLOG})
        self.assertEqual(res.status, 413)

    def test_malformed_json(self):
        for raw in (b"{", b"[1,2]", b"\xff\xfe"):
            res = self.client.post("/api/v1/comments", raw=raw, content_type="application/json",
                                   headers={"Origin": BLOG})
            self.assertEqual(res.status, 400, raw)

    # -- validation -------------------------------------------------------------------------
    def test_field_validation(self):
        cases = [
            ({"author": "   "}, "name_required"),
            ({"author": "\u200b\u3164"}, "name_required"),
            ({"author": "x" * 81}, "name_too_long"),
            ({"body": "  \n  "}, "body_required"),
            ({"body": "y" * 5001}, "body_too_long"),
            ({"email": "not-an-email"}, "email_invalid"),
            ({"thread": ""}, "invalid_thread"),
            ({"thread": "/a b"}, "invalid_thread"),
            ({"thread": "/a\u202eb"}, "invalid_thread"),
            ({"thread": "/" + "t" * 400}, "invalid_thread"),
        ]
        for fields, code in cases:
            with self.subTest(fields=fields):
                kwargs = {"author": "Ada", "body": "Hi", "email": "", "thread": "/posts/hello"}
                kwargs.update(fields)
                token = thread_token(self.client, "/posts/hello")
                res = post_comment(self.client, token=token, **kwargs)
                self.assertEqual(res.status, 400)
                self.assertEqual(res.json()["error"], code)

    def test_non_string_fields_do_not_crash(self):
        token = thread_token(self.client)
        res = post_comment(self.client, token=token,
                           extra={"author": ["x"], "body": {"a": 1}, "email": 5, "page": 7})
        self.assertIn(res.status, (201, 202, 400))

    def test_email_ignored_when_not_asked(self):
        self.app.settings.update({"ask_email": False})
        res = post_comment(self.client, email="ada@example.com")
        self.assertEqual(res.status, 201)
        self.assertIsNone(self.app.db.conn().execute("SELECT email FROM comments").fetchone()[0])

    # -- anti-bot ---------------------------------------------------------------------------
    def test_honeypot_pretends_success_and_stores_nothing(self):
        res = post_comment(self.client, extra={"website": "http://spam.example"})
        self.assertEqual(res.status, 202)
        self.assertEqual(res.json(), {"status": "pending"})
        self.assertEqual(self.app.db.conn().execute("SELECT COUNT(*) FROM comments").fetchone()[0], 0)
        self.assertEqual(self.app.db.counters_since(1).get("honeypot"), 1)

    def test_token_required_and_bound_to_thread(self):
        self.assertEqual(post_comment(self.client, token="").json()["error"], "form_expired")
        self.assertEqual(post_comment(self.client, token="1.2.3").json()["error"], "form_expired")
        other = thread_token(self.client, "/posts/other")
        self.assertEqual(post_comment(self.client, token=other).json()["error"], "form_expired")

    def test_token_is_single_use(self):
        token = thread_token(self.client)
        self.assertEqual(post_comment(self.client, token=token).status, 201)
        self.assertEqual(post_comment(self.client, token=token, body="Again").json()["error"], "form_expired")

    def test_response_hands_out_a_fresh_token(self):
        first = post_comment(self.client)
        second = post_comment(self.client, token=first.json()["token"], body="A follow-up.")
        self.assertEqual(second.status, 201)

    def test_too_fast(self):
        self.app.settings.update({"min_seconds": 5})
        res = post_comment(self.client)
        self.assertEqual(res.json()["error"], "too_fast")
        token = self.app.form_tokens.issue("/posts/hello", now=time.time() - 6)
        self.assertEqual(post_comment(self.client, token=token).status, 201)

    def test_old_token_expires(self):
        token = self.app.form_tokens.issue("/posts/hello", now=time.time() - 3 * 86400)
        self.assertEqual(post_comment(self.client, token=token).json()["error"], "form_expired")

    def test_rate_limit_per_sender_and_ipv6_prefix(self):
        self.app.settings.update({"rate_per_ip": 2})
        v6 = Client(self.app, remote_addr="2001:db8:1:2::1")
        self.assertEqual(post_comment(v6, body="one").status, 201)
        self.assertEqual(post_comment(v6, body="two").status, 201)
        v6.remote_addr = "2001:db8:1:2:ffff::9"  # same /64
        res = post_comment(v6, body="three")
        self.assertEqual(res.status, 429)
        self.assertIn("retry-after", res.headers)
        other = Client(self.app, remote_addr="2001:db8:9:9::1")
        self.assertEqual(post_comment(other, body="elsewhere").status, 201)

    def test_failed_validation_does_not_consume_rate_limit(self):
        self.app.settings.update({"rate_per_ip": 1})
        for _ in range(3):
            post_comment(self.client, body="")
        self.assertEqual(post_comment(self.client).status, 201)

    def test_global_rate_limit(self):
        self.app.settings.update({"rate_global": 2})
        for i in range(2):
            self.assertEqual(post_comment(Client(self.app, remote_addr=f"198.51.100.{i}"),
                                          body=f"n{i}").status, 201)
        self.assertEqual(post_comment(Client(self.app, remote_addr="198.51.100.99")).status, 429)

    def test_comments_closed(self):
        self.app.settings.update({"comments_open": False})
        thread = self.client.get("/api/v1/thread", query={"id": "/x"}, headers={"Origin": BLOG}).json()
        self.assertFalse(thread["open"])
        self.assertIsNone(thread["form"])
        token = self.app.form_tokens.issue("/posts/hello", now=time.time() - 10)
        self.assertEqual(post_comment(self.client, token=token).json()["error"], "comments_closed")

    # -- what the public can see ---------------------------------------------------------------
    def test_only_approved_comments_and_only_public_fields(self):
        self.app.settings.update({"max_links": 0})
        post_comment(self.client, body="Published one", email="secret@example.com")
        post_comment(self.client, body="Held: https://spam.example", email="secret2@example.com")
        data = self.client.get("/api/v1/thread", query={"id": "/posts/hello"},
                               headers={"Origin": BLOG}).json()
        self.assertEqual(data["count"], 1)
        comment = data["comments"][0]
        self.assertEqual(set(comment), {"id", "author", "created", "verified", "parent", "reply_to",
                                        "text", "body", "replies"})
        raw = json.dumps(data)
        for secret in ("secret@example.com", "ip_key", "laya", "reason", "203.0.113.7"):
            self.assertNotIn(secret, raw)

    def test_page_url_must_match_origin(self):
        post_comment(self.client, extra={"page": "https://evil.example/phish"})
        post_comment(self.client, body="Second", extra={"page": BLOG + "/posts/hello"})
        pages = [r[0] for r in self.app.db.conn().execute("SELECT page_url FROM comments ORDER BY id")]
        self.assertEqual(pages, [None, BLOG + "/posts/hello"])

    def test_raw_ip_never_stored(self):
        post_comment(self.client)
        with open(self.app.cfg.db_path, "rb") as fh:
            self.assertNotIn(b"203.0.113.7", fh.read())
        wal = self.app.cfg.db_path + "-wal"
        try:
            with open(wal, "rb") as fh:
                self.assertNotIn(b"203.0.113.7", fh.read())
        except FileNotFoundError:
            pass

    def test_security_headers_on_api(self):
        res = self.client.get("/api/v1/thread", query={"id": "/a"}, headers={"Origin": BLOG})
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'none'", res.headers["content-security-policy"])
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_widget_served_for_cross_origin_use(self):
        res = self.client.get("/widget.js")
        self.assertEqual(res.status, 200)
        self.assertTrue(res.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(res.headers["access-control-allow-origin"], "*")
        again = self.client.get("/widget.js", headers={"If-None-Match": res.headers["etag"]})
        self.assertEqual(again.status, 304)

    def test_method_not_allowed(self):
        self.assertEqual(self.client.request("DELETE", "/api/v1/comments",
                                             headers={"Origin": BLOG}).status, 405)


if __name__ == "__main__":
    unittest.main()
