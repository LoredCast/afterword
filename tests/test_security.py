import unittest

from afterword import security
from afterword.settings import normalize_origin


class SecurityTests(unittest.TestCase):
    def test_password_round_trip(self):
        stored = security.hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(security.verify_password("correct horse battery", stored))
        self.assertFalse(security.verify_password("correct horse batterY", stored))
        self.assertFalse(security.verify_password("x", None))
        self.assertFalse(security.verify_password("x", "garbage"))
        self.assertNotEqual(stored, security.hash_password("correct horse battery"))  # salted

    def test_form_token(self):
        tokens = security.FormTokens(b"k" * 32)
        t = tokens.issue("/a", now=1000)
        self.assertIsNone(tokens.check(t, "/a", 3, now=1005))
        self.assertEqual(tokens.check(t, "/a", 3, now=1001), "too_fast")
        self.assertEqual(tokens.check(t, "/b", 3, now=1005), "form_expired")
        self.assertEqual(tokens.check(t, "/a", 3, now=1000 + 3 * 86400), "form_expired")
        self.assertEqual(tokens.check(t[:-2] + "xx", "/a", 3, now=1005), "form_expired")
        other_key = security.FormTokens(b"j" * 32)
        self.assertEqual(other_key.check(t, "/a", 3, now=1005), "form_expired")
        tokens.consume(t, now=1005)
        self.assertEqual(tokens.check(t, "/a", 3, now=1006), "form_expired")

    def test_ip_key(self):
        key = b"s" * 32
        self.assertEqual(security.ip_key(key, "2001:db8::1"), security.ip_key(key, "2001:db8::ffff"))
        self.assertNotEqual(security.ip_key(key, "2001:db8::1"), security.ip_key(key, "2001:db8:0:1::1"))
        self.assertEqual(security.ip_key(key, "::ffff:192.0.2.1"), security.ip_key(key, "192.0.2.1"))
        self.assertNotEqual(security.ip_key(key, "192.0.2.1"), security.ip_key(b"t" * 32, "192.0.2.1"))
        self.assertEqual(len(security.ip_key(key, "not an ip")), 16)

    def test_rate_limiter_window(self):
        limiter = security.RateLimiter()
        self.assertEqual(limiter.hit("a", 2, 10, now=0), 0)
        self.assertEqual(limiter.hit("a", 2, 10, now=1), 0)
        self.assertGreater(limiter.hit("a", 2, 10, now=2), 0)
        self.assertEqual(limiter.hit("a", 2, 10, now=11), 0)

    def test_rate_limiter_is_bounded(self):
        limiter = security.RateLimiter(max_keys=100)
        for i in range(1000):
            limiter.hit(f"k{i}", 5, 60, now=i)
        self.assertLessEqual(len(limiter._hits), 100)

    def test_origin_normalisation(self):
        self.assertEqual(normalize_origin("HTTPS://Blog.Example.COM:443/"), "https://blog.example.com")
        self.assertEqual(normalize_origin("http://localhost:3000"), "http://localhost:3000")
        for bad in ("blog.example.com", "https://blog.example.com/path", "javascript:alert(1)",
                    "https://user@blog.example.com", "ftp://x.example", "https://x.example:99999"):
            self.assertIsNone(normalize_origin(bad), bad)


if __name__ == "__main__":
    unittest.main()
