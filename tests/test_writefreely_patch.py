"""The WriteFreely helper: insert after the post's </article>, idempotent, exact removal."""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "add_comments", os.path.join(HERE, "..", "contrib", "writefreely", "add-comments.py"))
add_comments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_comments)

# Shaped like WriteFreely's collection-post.tmpl (synthetic, not a copy).
TEMPLATE = """{{define "post"}}<!DOCTYPE HTML>
<html><body id="post">
\t\t<article id="post-body" class="{{.Font}} h-entry">{{if .Title.String}}<h2 id="title">{{.FormattedDisplayTitle}}</h2>{{end}}<div class="e-content">{{.HTMLContent}}</div></article>

\t\t{{ if .Collection.ShowFooterBranding }}<footer></footer>{{ end }}
\t</body>
</html>{{end}}
"""


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "templates"))
        self.path = os.path.join(self.dir, "templates", "collection-post.tmpl")
        with open(self.path, "w") as fh:
            fh.write(TEMPLATE)

    def read(self):
        with open(self.path) as fh:
            return fh.read()

    def run_script(self, *args):
        return add_comments.main([self.dir, *args])

    def test_inserts_after_article_once(self):
        self.assertEqual(self.run_script("https://comments.example.com"), 0)
        text = self.read()
        self.assertEqual(text.count("afterword:start"), 1)
        self.assertIn('</article>\n{{/* afterword:start */}}\n{{if and .IsFound (not .IsPinned)}}', text)
        self.assertIn('data-thread="{{.ID}}"', text)
        self.assertIn('src="https://comments.example.com/widget.js"', text)
        self.assertTrue(os.path.exists(self.path + ".before-afterword"))

    def test_rerun_is_a_no_op_and_address_can_change(self):
        self.run_script("https://comments.example.com")
        first = self.read()
        self.run_script("https://comments.example.com")
        self.assertEqual(self.read(), first)
        self.run_script("https://other.example.com/")
        self.assertEqual(self.read().count("afterword:start"), 1)
        self.assertIn("https://other.example.com/widget.js", self.read())

    def test_remove_restores_original_exactly(self):
        self.run_script("https://comments.example.com")
        self.run_script("--remove")
        self.assertEqual(self.read(), TEMPLATE)

    def test_rejects_unsafe_addresses(self):
        for bad in ('https://x.example/"><script>', "javascript:alert(1)", "comments.example.com"):
            with self.assertRaises(SystemExit):
                self.run_script(bad)
        self.assertEqual(self.read(), TEMPLATE)

    def test_template_without_article_is_left_alone(self):
        with open(self.path, "w") as fh:
            fh.write("<html></html>")
        self.assertEqual(self.run_script("https://comments.example.com"), 1)
        self.assertEqual(self.read(), "<html></html>")


if __name__ == "__main__":
    unittest.main()
