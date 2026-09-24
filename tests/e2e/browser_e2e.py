"""End-to-end test in a real browser.

Starts Afterword with its real CLI (waitress), serves a page shaped like a
WriteFreely post from a *different origin*, then drives Chromium through the
whole owner and reader journey:

  first-run password -> blog address in Settings -> reader posts a comment ->
  owner publishes it in the dashboard -> comment appears on the blog.

It also fails on any Content-Security-Policy violation or console error in
the dashboard, and saves screenshots to e2e-screenshots/.

    pip install playwright && playwright install chromium
    python tests/e2e/browser_e2e.py
"""
from __future__ import annotations

import http.server
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER, BLOG_PORT = "http://127.0.0.1:8081", 8090
BLOG = f"http://127.0.0.1:{BLOG_PORT}"
PASSWORD = "a long test passphrase"
SHOTS = os.path.join(ROOT, "e2e-screenshots")

# Markup shaped like WriteFreely's templates/collection-post.tmpl output, with
# the Afterword snippet pasted after </article> exactly as the dashboard says.
BLOG_PAGE = f"""<!DOCTYPE HTML>
<html lang="en" dir="auto">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>On keeping a notebook — Field Notes</title>
<link rel="canonical" href="{BLOG}/on-keeping-a-notebook" />
<style>
  /* A stand-in for a WriteFreely theme plus a little Custom CSS. */
  body#post {{ margin: 0; color: #333; background: #fff; font-family: Georgia, "Times New Roman", serif; }}
  body#post header {{ padding: 1em 1rem; }}
  #blog-title a {{ color: #333; text-decoration: none; font-size: 1.1rem; font-family: "Open Sans", Arial, sans-serif; }}
  article#post-body, #comments {{ max-width: 40rem; margin: 0 auto; padding: 0 1rem; font-size: 1.2em; line-height: 1.6; }}
  h2#title {{ font-size: 2em; line-height: 1.2; margin: 0.5em 0 0.25em; }}
  time.dt-published {{ color: #777; font-size: 0.8em; font-family: Arial, sans-serif; }}
  input, textarea {{ font: inherit; padding: 0.4em; border: 1px solid #ccc; border-radius: 0.25em; }}
  button {{ font-family: "Open Sans", Arial, sans-serif; font-size: 0.9em; padding: 0.5em 1.2em;
           border: 0; border-radius: 0.25em; background: #0070c9; color: #fff; cursor: pointer; }}
  a {{ color: #0070c9; }}
  /* Blog owner's custom CSS for comments */
  .afterword-heading {{ font-size: 1.3em; border-top: 1px solid #eee; padding-top: 1.5em; }}
  .afterword-meta {{ font-family: Arial, sans-serif; font-size: 0.75em; color: #777; }}
  .afterword-author {{ color: #333; }}
</style>
</head>
<body id="post">
<header><h1 id="blog-title"><a rel="author" href="/">Field Notes</a></h1></header>
<article id="post-body" class="norm h-entry"><h2 id="title" class="p-name dated">On keeping a notebook</h2><time class="dt-published" datetime="2026-09-20T09:00:00Z">September 20, 2026</time><div class="e-content"><p>I have carried a small notebook for eleven years. Most pages are lists. A few are the beginnings of essays that never happened, and some of those turned into posts here.</p><p>What surprised me is how rarely I reread it, and how much that doesn’t matter.</p></div></article>
<section id="comments" data-afterword data-thread="kqpz8r2nfw"></section>
<script src="{SERVER}/widget.js" defer></script>
<link rel="stylesheet" href="{SERVER}/afterword.css">
</body>
</html>"""


def serve_blog() -> http.server.HTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = BLOG_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", BLOG_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    os.makedirs(SHOTS, exist_ok=True)
    data = tempfile.mkdtemp(prefix="afterword-e2e-")
    env = dict(os.environ, AFTERWORD_DATA=data, AFTERWORD_LISTEN="127.0.0.1:8081", AFTERWORD_DEV="1",
               PYTHONPATH=ROOT)
    server = subprocess.Popen([sys.executable, "-m", "afterword", "serve"], env=env, cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log_lines: list[str] = []
    threading.Thread(target=lambda: [log_lines.append(l) for l in server.stdout], daemon=True).start()
    blog = serve_blog()
    problems: list[str] = []
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(SERVER + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        code = None
        for _ in range(50):
            match = re.search(r"setup code ([A-Z0-9-]{14})", "".join(log_lines))
            if match:
                code = match.group(1)
                break
            time.sleep(0.1)
        assert code, "setup code not printed:\n" + "".join(log_lines)

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            owner = browser.new_page(viewport={"width": 1100, "height": 900})
            owner.on("console", lambda m: problems.append(f"dashboard console {m.type}: {m.text}")
                     if m.type in ("error", "warning") else None)

            # 1. First run: the link from the log pre-fills the setup code.
            owner.goto(f"{SERVER}/admin/setup?code={code}")
            owner.fill("#password", PASSWORD)
            owner.fill("#confirm", PASSWORD)
            owner.click("text=Save password and continue")
            expect(owner.locator("h1")).to_have_text("Add comments to your blog")
            owner.screenshot(path=f"{SHOTS}/1-embed-instructions.png", full_page=True)

            # 2. Settings: add the blog's address.
            owner.click("nav >> text=Settings")
            owner.fill("#site_origins", BLOG)
            owner.click("text=Save settings")
            expect(owner.locator(".flash-ok")).to_have_text("Settings saved.")

            # 3. A reader comments on the blog (a different origin).
            reader = browser.new_page(viewport={"width": 900, "height": 1200})
            reader_errors: list[str] = []
            reader.on("pageerror", lambda e: reader_errors.append(str(e)))
            reader.goto(BLOG + "/on-keeping-a-notebook")
            expect(reader.locator(".afterword-form")).to_be_visible()
            expect(reader.locator(".afterword-empty")).to_have_text("No comments yet.")
            reader.fill(".afterword-field--name input", "Mara")
            reader.fill(".afterword-field--email input", "mara@example.org")
            reader.fill(".afterword-field--message textarea",
                        "Same here. My notebooks are mostly *lists* too.\n\n"
                        "I wrote about indexing them: https://mara.example.org/indexing.")
            time.sleep(3.2)  # the default minimum time before sending
            reader.click(".afterword-submit")
            expect(reader.locator(".afterword-notice--pending")).to_be_visible()
            reader.screenshot(path=f"{SHOTS}/2-blog-after-posting.png", full_page=True)

            # A hostile comment, submitted the same way.
            reader.fill(".afterword-field--message textarea",
                        '<img src=x onerror="document.title=\'pwned\'"> <script>document.title="pwned"</script>')
            time.sleep(3.2)
            reader.click(".afterword-submit")
            expect(reader.locator(".afterword-notice--pending")).to_be_visible()

            # 4. The owner publishes both from the queue.
            owner.click("nav >> text=Comments")
            expect(owner.locator(".comment")).to_have_count(2)
            owner.screenshot(path=f"{SHOTS}/3-dashboard-queue.png", full_page=True)
            owner.check("[data-select-all]")
            owner.select_option("#action", "approve")
            owner.click("text=Apply to selected")
            expect(owner.locator(".flash-ok")).to_have_text("Published 2 comments.")

            # 5. The blog shows them, rendered safely and styled by the blog's CSS.
            reader.reload()
            expect(reader.locator(".afterword-comment")).to_have_count(2)
            expect(reader.locator(".afterword-count")).to_have_text("2")
            expect(reader.locator(".afterword-body em")).to_have_text("lists")
            link = reader.locator(".afterword-body a")
            expect(link).to_have_attribute("href", "https://mara.example.org/indexing")
            expect(link).to_have_attribute("rel", "nofollow ugc noopener noreferrer")
            assert reader.title() != "pwned", "hostile comment executed"
            assert reader.locator("#comments img, #comments script").count() == 0
            button_bg = reader.eval_on_selector(".afterword-submit", "b => getComputedStyle(b).backgroundColor")
            assert button_bg == "rgb(0, 112, 201)", f"blog button style not applied: {button_bg}"
            reader.screenshot(path=f"{SHOTS}/4-blog-with-comments.png", full_page=True)

            owner.goto(SERVER + "/admin/laya")
            owner.screenshot(path=f"{SHOTS}/5-laya-page.png", full_page=True)
            owner.goto(SERVER + "/admin/settings")
            owner.screenshot(path=f"{SHOTS}/6-settings.png", full_page=True)

            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(SERVER + "/admin/login")
            mobile.fill("#password", PASSWORD)
            mobile.click("button:has-text('Sign in')")
            expect(mobile.locator("h1")).to_have_text("Comments")
            mobile.goto(SERVER + "/admin/comments?status=approved")
            expect(mobile.locator(".comment")).to_have_count(2)
            mobile.screenshot(path=f"{SHOTS}/7-dashboard-mobile.png", full_page=True)
            dark = browser.new_page(viewport={"width": 1100, "height": 900}, color_scheme="dark")
            dark.goto(SERVER + "/admin/login")
            dark.fill("#password", PASSWORD)
            dark.click("button:has-text('Sign in')")
            expect(dark.locator("h1")).to_have_text("Comments")
            dark.goto(SERVER + "/admin/comments?status=all")
            expect(dark.locator(".comment")).to_have_count(2)
            dark.screenshot(path=f"{SHOTS}/8-dashboard-dark.png", full_page=True)
            browser.close()

        problems += [f"blog page error: {e}" for e in reader_errors]
        if problems:
            raise AssertionError("\n".join(problems))
        print("E2E OK. Screenshots in", SHOTS)
    finally:
        blog.shutdown()
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
