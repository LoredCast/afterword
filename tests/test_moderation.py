import json
import time
import unittest

from afterword import moderation
from afterword.laya import LayaResult, build_request, parse_response
from afterword.settings import SPEC

from helpers import BLOG, Client, FakeLaya, cleanup, make_app, post_comment, use_fake_laya

DEFAULTS = {opt.key: opt.default for opt in SPEC}


def settings(**changes):
    values = dict(DEFAULTS)
    values.update(changes)
    return values


class DecideTests(unittest.TestCase):
    """The policy table, without any I/O."""

    def test_manual_always_pending(self):
        d = moderation.decide(settings(moderation_mode="manual"), [], LayaResult(ok=True, score=0.0))
        self.assertEqual((d.status, d.decided_by), ("pending", None))

    def test_assisted_never_decides(self):
        for score in (0.0, 0.5, 1.0):
            d = moderation.decide(settings(moderation_mode="assisted", laya_enabled=True), [],
                                  LayaResult(ok=True, score=score))
            self.assertEqual(d.status, "pending")
        d = moderation.decide(settings(moderation_mode="assisted", laya_enabled=True), [],
                              LayaResult(ok=True, score=0.8))
        self.assertIn("likely spam", d.reason)

    def test_automatic_basic(self):
        s = settings(moderation_mode="automatic", automatic_decider="basic")
        self.assertEqual(moderation.decide(s, [], None).status, "approved")
        held = moderation.decide(s, ["links:5"], None)
        self.assertEqual(held.status, "pending")
        self.assertIn("5 links", held.reason)

    def test_automatic_laya_thresholds(self):
        s = settings(moderation_mode="automatic", automatic_decider="laya", laya_enabled=True,
                     laya_approve_below=0.2, laya_reject_at=0.9)
        cases = [(0.05, "approved", "laya"), (0.2, "pending", None), (0.5, "pending", None),
                 (0.9, "rejected", "laya"), (0.99, "rejected", "laya")]
        for score, status, by in cases:
            with self.subTest(score=score):
                d = moderation.decide(s, [], LayaResult(ok=True, score=score))
                self.assertEqual((d.status, d.decided_by), (status, by))
                self.assertIn(f"{score:.2f}", d.reason)

    def test_flags_hold_even_when_laya_says_genuine(self):
        s = settings(moderation_mode="automatic", automatic_decider="laya", laya_enabled=True)
        d = moderation.decide(s, ["duplicate"], LayaResult(ok=True, score=0.01))
        self.assertEqual(d.status, "pending")

    def test_laya_failure_holds_by_default(self):
        s = settings(moderation_mode="automatic", automatic_decider="laya", laya_enabled=True)
        d = moderation.decide(s, [], LayaResult(ok=False, error="no answer within 3s"))
        self.assertEqual(d.status, "pending")
        self.assertIn("no answer within 3s", d.reason)
        self.assertIn("held", d.reason)

    def test_laya_failure_publish_fallback_respects_flags(self):
        s = settings(moderation_mode="automatic", automatic_decider="laya", laya_enabled=True,
                     laya_fallback="publish")
        self.assertEqual(moderation.decide(s, [], LayaResult(ok=False, error="x")).status, "approved")
        self.assertEqual(moderation.decide(s, ["links:9"], LayaResult(ok=False, error="x")).status, "pending")

    def test_laya_decider_while_laya_off_uses_fallback(self):
        s = settings(moderation_mode="automatic", automatic_decider="laya", laya_enabled=False)
        d = moderation.decide(s, [], None)
        self.assertEqual(d.status, "pending")
        self.assertIn("turned off", d.reason)

    def test_wants_laya(self):
        self.assertFalse(moderation.wants_laya(settings(moderation_mode="manual", laya_enabled=True)))
        self.assertTrue(moderation.wants_laya(settings(moderation_mode="assisted", laya_enabled=True)))
        self.assertFalse(moderation.wants_laya(settings(moderation_mode="assisted", laya_enabled=False)))
        self.assertTrue(moderation.wants_laya(settings(moderation_mode="automatic",
                                                       automatic_decider="laya", laya_enabled=False)))


class ProtocolTests(unittest.TestCase):
    def test_request_shape(self):
        req = build_request(settings(laya_model="english"), "Ada", "Hello")
        self.assertEqual(req["state"], {"author": "Ada", "comment": "Hello"})
        q = req["questions"]["spam"]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(set(q["criteria"]), {"A", "B"})
        self.assertEqual(req["model"], "english")
        self.assertNotIn("model", build_request(settings(laya_model="auto"), "a", "b"))

    def test_only_name_and_text_are_sent(self):
        req = json.dumps(build_request(settings(), "Ada", "Hello"))
        self.assertNotIn("email", req)

    def test_parse_rejects_bad_shapes(self):
        bad = [None, [], {}, {"answers": []}, {"answers": {"spam": {}}},
               {"answers": {"spam": {"probabilities": {"A": "0.1", "B": 0.9}}}},
               {"answers": {"spam": {"probabilities": {"A": True, "B": 0.9}}}},
               {"answers": {"spam": {"probabilities": {"A": 0.1, "B": 1.5}}}},
               {"answers": {"spam": {"probabilities": {"A": 0.1, "B": float("nan")}}}}]
        for data in bad:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    parse_response(data)

    def test_parse_normalises(self):
        score, model = parse_response({"answers": {"spam": {"probabilities": {"A": 0.25, "B": 0.25}}},
                                       "routing": {"model": "english"}})
        self.assertAlmostEqual(score, 0.5)
        self.assertEqual(model, "english")


class LayaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = Client(self.app)
        self.fake = FakeLaya()

    def tearDown(self):
        self.fake.close()
        cleanup(self.app)

    def row(self):
        return self.app.db.conn().execute("SELECT * FROM comments ORDER BY id DESC LIMIT 1").fetchone()

    def test_automatic_laya_publishes_holds_and_rejects(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake)
        self.assertEqual(post_comment(self.client, body="Thoughtful reply.").status, 201)
        self.assertEqual(self.row()["status"], "approved")
        res = post_comment(self.client, body="maybe something")
        self.assertEqual((res.status, self.row()["status"]), (202, "pending"))
        res = post_comment(self.client, body="Best online casino bonus!!!")
        self.assertEqual(res.status, 202)            # the spammer is told "pending"
        self.assertEqual(res.json(), {"status": "pending", "token": res.json()["token"]})
        row = self.row()
        self.assertEqual((row["status"], row["decided_by"]), ("rejected", "laya"))
        self.assertAlmostEqual(row["laya_score"], 0.97)
        self.assertIn("Rejected by Laya", row["reason"])
        sent = self.fake.requests[-1]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer test-key")
        self.assertNotIn("email", json.dumps(sent["body"]))

    def test_assisted_scores_but_never_publishes(self):
        self.app.settings.update({"moderation_mode": "assisted"})
        use_fake_laya(self.app, self.fake)
        self.assertEqual(post_comment(self.client, body="casino").status, 202)
        self.assertEqual(post_comment(self.client, body="Nice").status, 202)
        rows = self.app.db.conn().execute("SELECT status, laya_score FROM comments").fetchall()
        self.assertEqual([r["status"] for r in rows], ["pending", "pending"])
        self.assertTrue(all(r["laya_score"] is not None for r in rows))

    def test_manual_never_calls_laya(self):
        use_fake_laya(self.app, self.fake)
        self.app.settings.update({"moderation_mode": "manual"})
        post_comment(self.client)
        self.assertEqual(self.fake.requests, [])
        self.assertIsNone(self.row()["laya_status"])

    def assert_held_on_failure(self, expected_error: str):
        res = post_comment(self.client, body="Legit comment")
        self.assertEqual(res.status, 202)
        row = self.row()
        self.assertEqual(row["status"], "pending")      # stored, not lost
        self.assertEqual(row["laya_status"], "error")
        self.assertIn(expected_error, row["reason"])

    def test_timeout_holds(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake, laya_timeout=0.5)
        self.fake.delay = 1.5
        started = time.time()
        self.assert_held_on_failure("no answer within 0.5s")
        self.assertLess(time.time() - started, 1.4)

    def test_http_error_holds(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake)
        self.fake.mode = "error500"
        self.assert_held_on_failure("HTTP 500")

    def test_wrong_key_holds(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake, laya_api_key="wrong-key")
        self.assert_held_on_failure("API key")

    def test_garbage_and_missing_answers_hold(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        for mode in ("garbage", "missing"):
            with self.subTest(mode=mode):
                use_fake_laya(self.app, self.fake)
                self.fake.mode = mode
                self.assert_held_on_failure("unexpected answer")

    def test_unreachable_holds_then_cools_down(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake, laya_url="http://127.0.0.1:9")
        self.assert_held_on_failure("cannot reach Laya")
        self.assertTrue(self.app.laya.status()["cooling_down"])
        post_comment(self.client, body="Second legit comment")
        self.assertIn("failed recently", self.row()["reason"])

    def test_publish_fallback(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "laya"})
        use_fake_laya(self.app, self.fake, laya_fallback="publish")
        self.fake.mode = "error500"
        self.assertEqual(post_comment(self.client).status, 201)
        self.assertIn("fallback published", self.row()["reason"])

    def test_duplicate_and_blocked_terms_hold_in_automatic_basic(self):
        self.app.settings.update({"moderation_mode": "automatic", "automatic_decider": "basic",
                                  "blocked_terms": "Cheap Pills\n"})
        self.assertEqual(post_comment(self.client, body="Same words").status, 201)
        self.assertEqual(post_comment(self.client, body="  same   WORDS ").status, 202)
        self.assertIn("same text", self.row()["reason"])
        self.assertEqual(post_comment(self.client, body="buy cheap pills now").status, 202)
        self.assertIn("cheap pills", self.row()["reason"])


if __name__ == "__main__":
    unittest.main()
