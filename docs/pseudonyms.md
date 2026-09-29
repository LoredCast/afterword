# Pseudonyms

Pseudonyms let a reader keep a name without an account. The reader gives no
email address, no real name and no login from another site. A reader who keeps
a name can prove that later comments under it come from them. Nobody else can
post under that name, or under a name that looks like it.

The feature is **off by default**. Turn it on under **Settings → The comment
form → Let readers keep a name as a verified pseudonym**.

## What readers see

Under the name field, the comment form shows one checkbox:

> ☐ Keep this name as my pseudonym (?)

The explanations stay out of the way behind small **?** marks. Hovering a
**?** shows the text as a tooltip; clicking or tapping it (or Enter or Space)
shows it on the page. Screen readers announce it with the field.

- **The ? next to “Name”** gives the naming policy:

  > Names marked “verified” are pseudonyms. Only their holder can post under them, or under a name that looks like them. Any other name is marked “unverified”.

- **The ? next to the checkbox** says what keeping a name means:

  > Your browser will hold a secret key so that only you can post under this name here. No account, no email. Comments under a pseudonym are publicly linked to each other, and this site still sees your network address as with any comment. Save the backup key you are shown next: without it, clearing your browser or changing device can cost you the name.

- **Without ticking it**, a reader comments as before, under any name that is
  not someone's pseudonym. The comment is shown with an `unverified` mark. If
  the name is too close to a pseudonym, the error message states the policy.
- **After the first comment**, the form says *Posting as Mara verified* and opens the **backup key** so the reader can save it. They can
  copy the key or download it as a small text file. Later visits in the same
  browser keep the pseudonym without any action.
- **On another device**, or after clearing the browser, the reader opens
  **Restore a pseudonym from a backup key** and pastes the key. Pasting the
  whole backup file also works.
- **Stop using it on this device** removes the key from that browser, after a
  confirmation that warns about the backup.

Every comment posted with the key shows **verified** next to the name, as
plain text. While pseudonyms are on, every other comment shows **unverified**,
including every comment written before the feature was turned on.

## What "verified" means, and what it does not

- It means that **this comment was sent with the key** that first claimed the
  name. Every comment with the mark comes from whoever holds that key.
- It says **nothing about who the holder is**. Afterword does not know.
- It is **not anonymity**. All comments under one pseudonym are publicly
  linked to each other. The comment server handles the request exactly like
  any other comment. It sees the reader's network address and keeps the usual
  keyed network hash for rate limits (cleared after 30 days). If the blog asks
  for an email address, that is still optional and still only shown to you.
- It is **the comment server's word**. Readers trust your server to show the
  mark only where the key matched, just as they trust it to show the right
  text. A server that has been compromised could show anything.

## How it works

1. When a reader keeps a name, the widget makes a random key in the browser:
   32 characters, 160 bits, from `crypto.getRandomValues`. The key is stored in
   the blog's `localStorage` under `afterword:pseudonym`.
2. The widget sends the key with the comment (`"key"` in the JSON body of
   `POST /api/v1/comments`). The server keeps only
   `HMAC-SHA-256(server secret, key)`, never the key itself. If no pseudonym
   has that hash, and the name is free, the server claims the name together
   with the comment, in one transaction. If a pseudonym has the hash, the name
   sent must be its name.
3. The comment records which pseudonym it was posted with. The public API
   returns `"verified": true` for such comments and `false` for all others.
   This is decided once, when the comment arrives, so no comment is ever marked
   after the fact.
4. The key is sent only when posting a comment or restoring a backup. It is
   never sent when comments are loaded, so the server cannot use it to
   recognise someone who is only reading.

Restoring calls `POST /api/v1/pseudonym` with `{"key": "…"}`. It answers with
the pseudonym's name, or `404` if no pseudonym uses that key. Like posting, it
works only from your blog's addresses, and it is limited to 10 attempts per
sender every 10 minutes.

## The naming policy

Names are compared in a form that ignores differences readers could miss:

- Unicode compatibility forms (fullwidth letters, mathematical bold, …) and
  letter case.
- Accents.
- Spaces, punctuation, symbols and emoji.
- Common look-alike letters: Cyrillic and Greek letters that look Latin,
  `0`/`o`, `1`/`l`/`I`, `rn`/`m`, `vv`/`w`.

So once someone holds **Mara**, nobody can post as `mara`, `MARA`, `Mára`,
`M a r a`, `Mara!` or `Mаra` (with a Cyrillic а), with or without a key. A new
pseudonym needs at least one letter or digit. While pseudonyms are on, names
containing the word “verified” (in any spelling the comparison catches) or a
check mark (✓ ✔ ☑ ✅ √) are refused, so a name cannot imitate the mark.

No list of look-alikes is complete. Readers should rely on the “verified”
mark, not on the name alone. The widget shows the mark as a separate element,
next to the name, and every other name is marked “unverified”.

## Keeping and losing names

- A name is held **as long as at least one of its comments is kept** on the
  server. That includes pending and rejected comments. When the last one is
  deleted, the name is released within ten minutes. By default, rejected
  comments are deleted after 30 days. So names claimed by spam free
  themselves, and a claim cannot outlive its comments.
- **You can release any name** on the dashboard's **Pseudonyms** page. Its
  comments then lose the mark and show as unverified. Anyone can then use or
  claim the name. The comments keep their author name, but a later holder of
  the same name does not inherit them.
- **A reader who loses the key without a backup** cannot post under the name
  again, unless you release it and they claim it anew. You cannot tell the real
  holder from someone pretending to be them, so decide with care.
- A pseudonym belongs to one comment server. Its key is kept in that blog's
  browser storage. Readers should not use the same key on other sites: whoever
  runs a site where the key is used could post as that pseudonym here.

## Turning it off

Turning pseudonyms off hides the controls in the widget. The server then
refuses keys, and names are no longer reserved, so Afterword behaves as it did
before. Comments that were posted with a key keep their “verified” mark, because that
was true when they were sent. The names and key hashes stay in the database,
so turning pseudonyms on again restores everything. Readers' browsers keep
their keys while it is off.

## Dashboard

- Comments posted with a pseudonym carry a **verified** tag in the queue.
  Click it to see every comment from that holder.
- The **Pseudonyms** page lists each held name, how long it has been held and
  how many comments it has in each state, with a **Release** button. The page
  appears in the navigation while the feature is on, or while any names are
  held.
- Blocked terms, rate limits, Laya and every moderation mode apply to
  pseudonym comments exactly as to any other comment. A pseudonym grants no
  moderation privileges.
