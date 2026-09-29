"""End-to-end test in a real browser.

Starts Afterword with its real CLI (waitress), serves a page shaped like a
WriteFreely post from a *different origin*, then drives Chromium through the
whole owner and reader journey:

  first-run password -> blog address in Settings -> reader posts a comment ->
  owner publishes it in the dashboard -> comment appears on the blog ->
  owner turns on pseudonyms -> reader keeps a name -> a second browser cannot
  use it -> the reader restores it there from the backup key -> verified marks ->
  a reply, and a reply to that reply, shown one level deep with @references.

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
            owner.fill("#rate_per_ip", "20")   # every reader in this test shares 127.0.0.1
            owner.click("text=Save settings")
            expect(owner.locator(".flash-ok")).to_have_text("Settings saved.")

            # 3. A reader comments on the blog (a different origin).
            reader = browser.new_page(viewport={"width": 900, "height": 1200})
            reader_errors: list[str] = []
            reader.on("pageerror", lambda e: reader_errors.append(str(e)))
            reader.goto(BLOG + "/on-keeping-a-notebook")
            expect(reader.locator(".afterword-form")).to_be_hidden()      # collapsed until wanted
            expect(reader.locator(".afterword-empty")).to_have_text("No comments yet.")
            reader.click(".afterword-compose-button")
            expect(reader.locator(".afterword-form")).to_be_visible()
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

            # 6. Pseudonyms: the owner turns them on; older comments stay unverified.
            owner.goto(SERVER + "/admin/settings")
            owner.check("#pseudonyms")
            owner.click("text=Save settings")
            expect(owner.locator(".flash-ok")).to_have_text("Settings saved.")
            reader_requests: list = []
            reader.on("request", lambda r: reader_requests.append(r))
            reader.reload()
            expect(reader.locator(".afterword-unverified")).to_have_count(2)
            expect(reader.locator(".afterword-verified")).to_have_count(0)

            # The reader keeps "Mara" as a pseudonym with their next comment.
            reader.click(".afterword-compose-button")
            reader.fill(".afterword-field--name input", "Mara")
            reader.check(".afterword-field--keep input")
            expect(reader.locator(".afterword-pseudonym-help")).to_contain_text("publicly linked")
            reader.screenshot(path=f"{SHOTS}/9-blog-keep-pseudonym.png", full_page=True)
            reader.fill(".afterword-field--message textarea", "Now with a name that stays mine.")
            time.sleep(3.2)
            reader.click(".afterword-submit")
            expect(reader.locator(".afterword-notice--pending")).to_be_visible()
            expect(reader.locator(".afterword-posting-as")).to_contain_text("Posting as Mara")
            backup_key = reader.input_value(".afterword-backup-key")
            assert re.fullmatch(r"(?:[a-z2-7]{4}-){7}[a-z2-7]{4}", backup_key), backup_key
            with reader.expect_download() as download_info:
                reader.click(".afterword-download")
            backup_path = download_info.value.path()
            with open(backup_path, encoding="utf-8") as fh:
                backup_text = fh.read()
            assert backup_key in backup_text and "Name: Mara" in backup_text, backup_text
            reader.screenshot(path=f"{SHOTS}/10-blog-backup-key.png", full_page=True)
            reader.reload()   # kept across visits
            reader.click(".afterword-compose-button")
            expect(reader.locator(".afterword-posting-as")).to_contain_text("Posting as Mara")
            # Only posting a comment sends the key; loading the page and its comments never does.
            raw_key = backup_key.replace("-", "")
            for request in reader_requests:
                assert raw_key not in request.url, request.url
                if "/api/v1/comments" not in request.url:
                    assert raw_key not in (request.post_data or ""), request.url

            # A second browser cannot post as Mara, or as a look-alike...
            other_context = browser.new_context(viewport={"width": 900, "height": 1200})
            other = other_context.new_page()
            other.on("pageerror", lambda e: reader_errors.append(str(e)))
            other.goto(BLOG + "/on-keeping-a-notebook")
            other.click(".afterword-compose-button")
            other.fill(".afterword-field--name input", "MARA")
            other.fill(".afterword-field--message textarea", "It's me, honest.")
            time.sleep(3.2)
            other.click(".afterword-submit")
            expect(other.locator(".afterword-notice--error")).to_contain_text("too close to someone")
            # ...until the reader restores the pseudonym there from the backup file.
            other.click(".afterword-restore summary")
            other.fill(".afterword-field--restore input", backup_text)
            other.click(".afterword-restore-button")
            expect(other.locator(".afterword-posting-as")).to_contain_text("Posting as Mara")
            other.click(".afterword-submit")
            expect(other.locator(".afterword-notice--pending")).to_be_visible()

            # The owner sees both under one pseudonym and publishes them.
            owner.goto(SERVER + "/admin/comments")
            expect(owner.locator(".comment .tag-pseudonym")).to_have_count(2)
            owner.check("[data-select-all]")
            owner.select_option("#action", "approve")
            owner.click("text=Apply to selected")
            expect(owner.locator(".flash-ok")).to_have_text("Published 2 comments.")
            owner.click("nav >> text=Pseudonyms")
            expect(owner.locator("table.pseudonyms tbody tr")).to_have_count(1)
            owner.screenshot(path=f"{SHOTS}/11-dashboard-pseudonyms.png", full_page=True)

            reader.reload()
            expect(reader.locator(".afterword-comment")).to_have_count(4)
            expect(reader.locator(".afterword-comment--verified")).to_have_count(2)
            expect(reader.locator(".afterword-comment--verified .afterword-verified").first).to_have_text(
                "verified")
            expect(reader.locator(".afterword-unverified")).to_have_count(2)
            reader.screenshot(path=f"{SHOTS}/12-blog-verified.png", full_page=True)
            other_context.close()

            # 7. Replies: Grace answers the first comment...
            grace_context = browser.new_context(viewport={"width": 900, "height": 1200})
            grace = grace_context.new_page()
            grace.on("pageerror", lambda e: reader_errors.append(str(e)))
            grace.goto(BLOG + "/on-keeping-a-notebook")
            first = grace.locator(".afterword-list > .afterword-comment").first
            first.locator(":scope > .afterword-comment-actions .afterword-reply-button").click()
            expect(grace.locator(".afterword-form")).to_be_visible()
            expect(grace.locator(".afterword-replying")).to_contain_text("Replying to @Mara \u00b7 ")
            grace.fill(".afterword-field--name input", "Grace")
            grace.fill(".afterword-field--message textarea", "Lists are underrated.")
            time.sleep(3.2)
            grace.click(".afterword-submit")
            expect(grace.locator(".afterword-notice--pending")).to_be_visible()
            owner.goto(SERVER + "/admin/comments")
            expect(owner.locator(".comment .comment-reply")).to_contain_text("Reply to @Mara")
            owner.click("button:has-text('Publish')")
            expect(owner.locator(".flash-ok")).to_have_text("Published 1 comment.")

            # ...and Mara answers Grace's reply: it joins the same conversation, naming Grace.
            reader.reload()
            grace_reply = reader.locator(".afterword-comment--reply", has_text="Lists are underrated.")
            grace_reply.locator(".afterword-reply-button").click()
            expect(reader.locator(".afterword-replying")).to_contain_text("Replying to @Grace")
            reader.fill(".afterword-field--message textarea", "They are. Thanks, Grace.")
            time.sleep(3.2)
            reader.click(".afterword-submit")
            expect(reader.locator(".afterword-notice--pending")).to_be_visible()
            expect(reader.locator(".afterword-replying")).to_be_hidden()
            owner.goto(SERVER + "/admin/comments")
            owner.click("button:has-text('Publish')")
            expect(owner.locator(".flash-ok")).to_have_text("Published 1 comment.")
            reader.reload()
            conversation = reader.locator(".afterword-list > .afterword-comment").first
            replies = conversation.locator(":scope > .afterword-replies > .afterword-comment--reply")
            expect(replies).to_have_count(2)
            expect(replies.nth(0).locator(".afterword-reply-to")).to_contain_text("@Mara \u00b7 ")
            expect(replies.nth(1).locator(".afterword-reply-to")).to_contain_text("@Grace \u00b7 ")
            expect(replies.nth(1).locator(".afterword-verified")).to_have_text("verified")
            expect(reader.locator(".afterword-replies .afterword-replies")).to_have_count(0)
            expect(reader.locator(".afterword-count")).to_have_text("6")
            assert reader.eval_on_selector(".afterword-reply-button", "b => getComputedStyle(b).backgroundColor") \
                in ("rgba(0, 0, 0, 0)", "transparent"), "reply buttons should look like quiet links"
            reader.screenshot(path=f"{SHOTS}/13-blog-replies.png", full_page=True)
            grace_context.close()

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
            expect(mobile.locator(".comment")).to_have_count(6)
            mobile.screenshot(path=f"{SHOTS}/7-dashboard-mobile.png", full_page=True)
            dark = browser.new_page(viewport={"width": 1100, "height": 900}, color_scheme="dark")
            dark.goto(SERVER + "/admin/login")
            dark.fill("#password", PASSWORD)
            dark.click("button:has-text('Sign in')")
            expect(dark.locator("h1")).to_have_text("Comments")
            dark.goto(SERVER + "/admin/comments?status=all")
            expect(dark.locator(".comment")).to_have_count(6)
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
