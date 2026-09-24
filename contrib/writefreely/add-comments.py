#!/usr/bin/env python3
"""Add (or remove) Afterword comments in a self-hosted WriteFreely install.

    python3 add-comments.py /srv/writefreely https://comments.example.com
    python3 add-comments.py /srv/writefreely https://comments.example.com --sri
    python3 add-comments.py /srv/writefreely --remove

Edits templates/collection-post.tmpl and templates/chorus-collection-post.tmpl,
inserting the snippet right after the post's </article>. It is safe to run
again: an existing Afterword block is replaced, never duplicated. The first
run keeps a copy of each original as <name>.before-afterword.

WriteFreely reads templates when it starts, so restart it afterwards. Upgrades
replace the templates; run this script again after each upgrade.

Standard library only, so it runs wherever WriteFreely does.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shutil
import sys
import urllib.request
from urllib.parse import urlsplit

TEMPLATES = ("collection-post.tmpl", "chorus-collection-post.tmpl")
START = "{{/* afterword:start */}}"
END = "{{/* afterword:end */}}"
# Inserted as "\n" + block directly after </article>, and removed the same way,
# so adding then removing restores the original file byte for byte.
BLOCK_RE = re.compile(r"\n?" + re.escape(START) + r".*?" + re.escape(END), re.S)
# The post body is the first <article id="post-body" ...>...</article> in both templates.
ARTICLE_END_RE = re.compile(r'(<article id="post-body"[^>]*>.*?</article>)', re.S)


def snippet(server: str, integrity: str | None) -> str:
    extra = f' integrity="{integrity}" crossorigin="anonymous"' if integrity else ""
    return (
        f"{START}\n"
        "{{if and .IsFound (not .IsPinned)}}\n"
        '<section id="comments" data-afterword data-thread="{{.ID}}"></section>\n'
        f'<script src="{server}/widget.js"{extra} defer></script>\n'
        "{{end}}\n"
        f"{END}"
    )


def fetch_integrity(server: str) -> str:
    with urllib.request.urlopen(server + "/widget.js", timeout=15) as resp:
        data = resp.read()
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def patch(text: str, block: str | None) -> str:
    text = BLOCK_RE.sub("", text)
    if block is None:
        return text
    match = ARTICLE_END_RE.search(text)
    if not match:
        raise ValueError('could not find <article id="post-body">…</article>')
    return text[:match.end()] + "\n" + block + text[match.end():]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("writefreely_dir", help="the folder containing WriteFreely's templates/ folder")
    parser.add_argument("server", nargs="?", help="Afterword's address, e.g. https://comments.example.com")
    parser.add_argument("--remove", action="store_true", help="remove the Afterword block")
    parser.add_argument("--sri", action="store_true",
                        help="pin the widget with a Subresource Integrity hash (re-run after upgrading Afterword)")
    args = parser.parse_args(argv)

    block = None
    if not args.remove:
        if not args.server:
            parser.error("give Afterword's address, or --remove")
        server = args.server.rstrip("/")
        parts = urlsplit(server)
        if parts.scheme not in ("http", "https") or not parts.netloc or '"' in server or "{" in server:
            parser.error("the address must look like https://comments.example.com")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
            print("Warning: loading the widget over plain http lets anyone on the network change it.",
                  file=sys.stderr)
        block = snippet(server, fetch_integrity(server) if args.sri else None)

    folder = os.path.join(args.writefreely_dir, "templates")
    changed = 0
    for name in TEMPLATES:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        try:
            updated = patch(original, block)
        except ValueError as exc:
            print(f"{path}: {exc}; edit it by hand (see the dashboard's “Add to your blog” page).",
                  file=sys.stderr)
            return 1
        if updated == original:
            print(f"{path}: already up to date")
            continue
        backup = path + ".before-afterword"
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(updated)
        changed += 1
        print(f"{path}: {'removed Afterword' if args.remove else 'added Afterword'}")
    if not changed:
        print("Nothing changed.")
    else:
        print("Restart WriteFreely to load the new templates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
