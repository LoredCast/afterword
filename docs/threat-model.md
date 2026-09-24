# Threat model

This document says what Afterword protects, from whom, how, and where the
protection stops. "Secure" here means: a small, constrained feature set, a
documented model of the threats, safeguards that are tested, and failure modes
that are safe. It does not mean exploitation is impossible.

## What is being protected

| Asset | Why it matters |
|---|---|
| Readers of the blog | Comments are displayed on the blog's own pages, in the blog's origin. Script injection there would compromise every visitor. |
| The blog owner's dashboard session | Whoever holds it can publish anything under the blog's name. |
| Commenters' email addresses | Collected optionally and promised never to be shown. |
| Comment integrity and availability | Comments should not be lost, altered or silently dropped. |
| The server | Afterword runs on the owner's machine, sometimes next to WriteFreely. |

## Who might attack

- **Spammers and bots**: high volume, automated, low effort. The main day-to-day threat.
- **A malicious commenter**: crafts input to run script on the blog or in the dashboard, spoof other people, or mislead readers.
- **A malicious website**: tries to use a visitor's browser to post comments, or to act in a logged-in owner's dashboard (CSRF).
- **A network attacker**: sits between reader, blog and comment server.
- **An attacker who compromises the comment server** (out of scope to prevent entirely; see residual risks).

## Trust boundaries

```
reader's browser ──(blog origin)──> blog page ──loads──> widget.js (comment origin)
       │                                                   │
       └───────── JSON over CORS, no cookies ─────────────>│ Afterword ──> SQLite file
                                                           │     │
owner's browser ──(comment origin, cookie, CSRF token)────>│     └──> Laya (loopback HTTP, API key)
```

Everything a commenter sends is untrusted. Everything Laya returns is also
treated as untrusted data and validated before use.

## Threats and mitigations

Each safeguard names the test that checks it (`tests/…`).

### Script injection into the blog (stored XSS)

- Comments are stored as plain text and **never turned into HTML by the server**. The API returns a small token tree (text, line break, emphasis, code, link). *test_text.RenderTests*
- The widget builds the thread only with `createElement`, `textContent` and `createTextNode`; it contains no `innerHTML`, `insertAdjacentHTML`, `document.write` or `eval`. Unknown token types fall back to text. *tests/js "hostile server data can never become markup"*
- Links are allowed only for `http:`/`https:` URLs with an ASCII host and no `user:password@`. The server checks this and the widget checks again, so even a tampered API response cannot produce a `javascript:` link. Links always carry `rel="nofollow ugc noopener noreferrer"`. *test_dangerous_schemes_never_linked, test_credentials_and_idn_hosts_not_linked, widget test*
- Tested end to end in Chromium with a live `<img onerror>`/`<script>` comment. *tests/e2e/browser_e2e.py*

### Script injection into the dashboard

- All dashboard HTML goes through an auto-escaping renderer: a value is escaped unless it is explicitly marked safe. *test_hostile_comment_is_escaped_everywhere*
- Strict Content-Security-Policy: `default-src 'none'; script-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`. No inline scripts, styles or handlers exist to allow. *test_security_headers*; the browser test fails on any CSP violation.
- Comment text is shown raw (not rendered) to the owner, so disguised links and tricks are visible.

### Deceptive text

- Bidirectional override and isolate characters (the "Trojan Source" class), zero-width and other invisible format characters, control characters, lone surrogates and noncharacters are removed. Runs of combining marks are capped ("Zalgo" text). Names made only of blank look-alike characters are refused. *CleanTests*
- Links to non-ASCII hosts are shown as text, not linked, because they can imitate other domains.

### Cross-site request forgery and clickjacking

- Every dashboard form carries a per-session CSRF token, **and** every POST must come from the dashboard's own origin (checked via `Origin`, `Sec-Fetch-Site` and `Referer`). *test_post_without_csrf_rejected, test_cross_site_post_rejected_even_with_token*
- Session cookies are `__Host-` prefixed, `Secure`, `HttpOnly`, `SameSite=Strict`, so sibling subdomains cannot set or read them. *test_session_cookie_is_hardened*
- `frame-ancestors 'none'` and `X-Frame-Options: DENY` prevent framing.

### Using other sites to post

- The API accepts comments only when the browser's `Origin` is one of the blog addresses in Settings; CORS headers are sent only to those. Requests never carry cookies. *test_post_requires_allowed_origin, test_preflight*
- This stops other websites from making *visitors' browsers* post. It does not stop a bot that forges the header; that is what the spam defences below are for.

### Spam and floods

- Hidden honeypot field: bots that fill it get a normal-looking answer and nothing is stored. *test_honeypot_pretends_success_and_stores_nothing*
- Signed, single-use form tokens bound to the thread, with a minimum age (default 3 s) and maximum age (2 days). *test_token_*, *test_too_fast*
- Rate limits per sender (IPv6 grouped by /64) and globally. Failed validation does not use up a reader's allowance. *test_rate_limit_per_sender_and_ipv6_prefix, test_global_rate_limit*
- Basic checks that hold (never reject) a comment in automatic mode: too many links, blocked terms, repeated text. *test_duplicate_and_blocked_terms_hold_in_automatic_basic*
- Optional Laya scoring. Spammers are told "awaiting review" whether their comment was held or rejected.
- Request bodies are capped (64 KB for comments, 1 MB overall); comment and name lengths are capped. *test_oversized_request_rejected, test_field_validation*

### Forged client addresses

- `X-Forwarded-For` is trusted only from the configured proxy (default `127.0.0.1`); otherwise the connection address is used. Verified against the real server: forged headers are ignored when no proxy is trusted.

### Dashboard password attacks

- scrypt password hashing (N=2^15, r=8), minimum 12 characters, constant-time comparison. *test_password_round_trip*
- Login throttled per sender (10 per 15 min) and globally (60 per hour). *test_wrong_password_and_lockout*
- First-run setup requires a one-time code printed in the server log (expires after 24 h), so nobody can claim a fresh install over the network. *test_setup_with_code*
- Changing the password signs out all other sessions. *test_password_change_signs_out_other_sessions*

### Privacy

- Email addresses are never returned by the public API and never sent to Laya. *test_only_approved_comments_and_only_public_fields, test_only_name_and_text_are_sent*
- Raw IP addresses are never stored: a keyed hash of the sender's network is kept for rate limiting and cleared from comments after 30 days. *test_raw_ip_never_stored, test_maintenance_purges_and_forgets*
- The widget sends the page address without query string or fragment, and makes no requests anywhere except the comment server.

### Laya

- Laya runs as a separate process, bound to `127.0.0.1` (overriding `laya-serve`'s default of `0.0.0.0`) with a random API key, and does not inherit Afterword's environment. *test_laya_manager.test_lifecycle*
- Its answers are parsed strictly: wrong shapes, non-numbers, NaN, out-of-range values, oversized or non-JSON responses are all treated as "Laya unavailable". *test_parse_rejects_bad_shapes, test_garbage_and_missing_answers_hold*
- Laya never decides anything in manual or assisted mode. In automatic mode its rejections go to the Rejected folder, not to deletion.

## Safe failure

| When this fails | Afterword does this |
|---|---|
| Laya is not installed, stopped, slow, crashing, or answering nonsense | The comment is already saved as pending before Laya is asked. It stays pending with the reason recorded ("no answer within 3s"), unless the owner chose "publish if the basic checks pass". After an error Laya is left alone for 30 s. *test_timeout_holds, test_http_error_holds, test_unreachable_holds_then_cools_down* |
| The managed Laya process keeps crashing | Restarted with backoff three times, then shown as "Needs attention" with its log. |
| The comment server is down | The widget shows "Comments could not be loaded." The blog is unaffected. |
| A reader's submission is invalid | A specific message; nothing is stored; their rate allowance is not used. |
| A setting is invalid | Nothing is saved; the form shows what to fix. A corrupt stored value falls back to the default. |
| An unexpected server error | A generic message to the client; details only in the server log. |

## Residual risks and non-goals

- **A compromised comment server can serve a malicious `widget.js`** to every reader of the blog. Mitigations: keep the server updated and isolated (the systemd unit is hardened), and optionally pin the widget with Subresource Integrity (the dashboard shows the snippet) so the blog refuses a modified file.
- **Installing Laya from the dashboard downloads and runs code** from PyPI (PyTorch, Laya and their dependencies) and model weights from Hugging Face. The Laya version is pinned, but its dependencies are not hash-pinned. Set `AFTERWORD_LAYA_INSTALL=0` to forbid this and run Laya yourself.
- **Laya can be wrong or deliberately evaded.** Its own documentation says scores are not calibrated for a given site out of the box. Assisted mode keeps a human in charge; the dashboard shows how often Laya agreed with your past decisions.
- **Determined, distributed spammers** can pass the basic checks; the global rate limit and moderation queue are the backstop. There is no CAPTCHA, by design.
- **Impersonation**: names are not verified. Anyone can post as "Alice". This is inherent to account-free commenting.
- **Backups contain email addresses** and the server's secret key; store them privately.
- **Denial of service** beyond the built-in limits (e.g. large-scale traffic floods) should be handled by the reverse proxy or network.
- **Single administrator**: there are no roles or audit log beyond the stored decision reasons.

## Reporting a problem

Please report security issues privately to the maintainer rather than in a public issue.
