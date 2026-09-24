import unittest

from afterword import text


def flat(paragraphs):
    return [[(s["t"], s.get("v")) for s in p] for p in paragraphs]


class CleanTests(unittest.TestCase):
    def test_bidi_overrides_removed(self):
        tricky = "admin\u202e\u2066gnp.exe\u2069"
        self.assertEqual(text.clean(tricky, multiline=False), "admingnp.exe")

    def test_control_characters_removed_but_newlines_kept_in_body(self):
        raw = "line one\x00\x07\r\nline two\x1b[31m"
        self.assertEqual(text.clean(raw, multiline=True), "line one\nline two[31m")

    def test_names_are_single_line(self):
        self.assertEqual(text.clean("  Ada\n\tLovelace  ", multiline=False), "Ada Lovelace")

    def test_zero_width_and_soft_hyphen_removed_but_emoji_joiners_kept(self):
        family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
        self.assertEqual(text.clean("ca\u00adsi\u200bno " + family, multiline=True), "casino " + family)

    def test_flag_tag_sequences_survive(self):
        england = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
        self.assertEqual(text.clean(england, multiline=False), england)

    def test_zalgo_is_capped(self):
        zalgo = "a" + "\u0301" * 50
        self.assertEqual(len(text.clean(zalgo, multiline=False)), 1 + text.MAX_COMBINING_RUN)

    def test_vietnamese_and_devanagari_intact(self):
        for sample in ("Tiếng Việt có dấu", "नमस्ते दुनिया"):
            self.assertEqual(text.clean(sample, multiline=False), sample)

    def test_blank_lookalike_names_are_not_visible(self):
        cleaned = text.clean("\u3164\u2800\u200b", multiline=False)
        self.assertFalse(text.has_visible_text(cleaned))

    def test_excess_blank_lines_collapsed(self):
        self.assertEqual(text.clean("a\n\n\n\n\nb   \n", multiline=True), "a\n\nb")

    def test_unicode_line_separators(self):
        self.assertEqual(text.clean("a\u2028b\u2029c", multiline=True), "a\nb\n\nc")

    def test_lone_surrogates_and_noncharacters_dropped(self):
        self.assertEqual(text.clean("a\ud800b\ufffec\U0001fffe", multiline=True), "abc")

    def test_idempotent(self):
        sample = "Hello\u202e  world\n\n\n\u0301\u0301 ok"
        once = text.clean(sample, multiline=True)
        self.assertEqual(text.clean(once, multiline=True), once)


class RenderTests(unittest.TestCase):
    def test_html_is_just_text(self):
        body = '<script>alert(1)</script><img src=x onerror=alert(1)>'
        self.assertEqual(flat(text.render(body)), [[("text", body)]])

    def test_paragraphs_and_line_breaks(self):
        self.assertEqual(flat(text.render("a\nb\n\nc", formatting="plain")),
                         [[("text", "a"), ("br", None), ("text", "b")], [("text", "c")]])

    def test_plain_mode_has_no_markup(self):
        self.assertEqual(flat(text.render("*hi* `x` https://example.com", formatting="plain")),
                         [[("text", "*hi* `x` https://example.com")]])

    def test_emphasis_and_code(self):
        self.assertEqual(flat(text.render("I *really* like `print()` here")),
                         [[("text", "I "), ("em", "really"), ("text", " like "), ("code", "print()"),
                           ("text", " here")]])

    def test_arithmetic_is_not_emphasis(self):
        self.assertEqual(flat(text.render("2*3*4 and a * b * c")), [[("text", "2*3*4 and a * b * c")]])

    def test_code_is_literal(self):
        self.assertEqual(flat(text.render("`<b>*x*</b> https://a.example`")),
                         [[("code", "<b>*x*</b> https://a.example")]])

    def test_links_trim_trailing_punctuation(self):
        self.assertEqual(flat(text.render("See https://example.com/a_(b), then.")),
                         [[("text", "See "), ("a", "https://example.com/a_(b)"), ("text", ", then.")]])

    def test_link_in_parentheses(self):
        self.assertEqual(flat(text.render("(https://example.com/x)")),
                         [[("text", "("), ("a", "https://example.com/x"), ("text", ")")]])

    def test_dangerous_schemes_never_linked(self):
        for body in ("javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x",
                     "JaVaScRiPt:alert(1)", "//evil.example/x", "file:///etc/passwd"):
            with self.subTest(body=body):
                segments = [s for p in text.render(body) for s in p]
                self.assertFalse([s for s in segments if s["t"] == "a"], segments)

    def test_credentials_and_idn_hosts_not_linked(self):
        for url in ("https://paypal.com@evil.example/login", "https://аpple.com/",
                    "https://example.com:99999/"):
            with self.subTest(url=url):
                segments = [s for p in text.render(url) for s in p]
                self.assertEqual(segments, [{"t": "text", "v": url}])

    def test_linkify_off(self):
        self.assertEqual(flat(text.render("https://example.com", linkify=False)),
                         [[("text", "https://example.com")]])

    def test_quote_cannot_break_out(self):
        segments = [s for p in text.render('https://example.com/"onmouseover="alert(1)') for s in p]
        links = [s["v"] for s in segments if s["t"] == "a"]
        self.assertEqual(links, ["https://example.com/"])

    def test_count_links(self):
        self.assertEqual(text.count_links("http://a https://b www.c [url=x] <a href=y>"), 5)
        self.assertEqual(text.count_links("no links here"), 0)


if __name__ == "__main__":
    unittest.main()
