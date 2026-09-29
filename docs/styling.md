# Embedding and styling the widget

The widget adds ordinary elements to your page: headings, a list, a form with
labels, inputs and a button. There is no iframe and no shadow DOM, so your
blog's existing CSS already applies. Every element also carries an
`afterword-…` class for anything you want to adjust.

The widget injects **no CSS of its own**, apart from the inline positioning
that hides the spam-trap field. For a neutral starting point, link
`https://comments.example.com/afterword.css` or copy it into your own stylesheet.

## Embedding

```html
<div data-afterword data-thread="post-123"></div>
<script src="https://comments.example.com/widget.js" defer></script>
```

The script finds every element with `data-afterword` on the page. It learns the
server's address from its own `src`, so you only type it once. Sub-paths work:
if the script is at `https://example.com/comments/widget.js`, the API is
expected at `https://example.com/comments/api/…`.

For pages that add content later, call `Afterword.init()` (or
`Afterword.init(someElement)`) after inserting a new container.

### Attributes on the container

| Attribute | Meaning |
|---|---|
| `data-afterword` | Required. Marks the container. |
| `data-thread="…"` | The thread identifier. In WriteFreely use `{{.ID}}`, the permanent post id. |
| `data-thread-from="path"` | Default when `data-thread` is absent: the page's path, without a trailing slash (`/my-post`). Query strings and fragments are ignored. |
| `data-thread-from="canonical"` | The path of the page's `<link rel="canonical">`. Useful when one post is reachable at several addresses. |
| `data-server="https://…"` | Override the server address (normally taken from the script's URL). |
| `data-heading-level="2"` | Level of the "Comments" heading (2–6). The form heading is one level lower. |
| `data-form="open"` | Show the comment form straight away. By default it is collapsed behind a “Write a comment” link, so only the comments are open. |
| `data-remember="true"` | Remember the reader's name and email in their browser (localStorage) for next time. Off by default. |
| `data-text-…="…"` | Replace any piece of text; see below. |

Thread identifiers can be up to 300 characters with no spaces. Pick one scheme
and keep it: comments are attached to the identifier, not to the page.

### Changing the text (and translating it)

Every string can be replaced with a `data-text-` attribute. Set a heading to an
empty string to leave it out.

```html
<div data-afterword data-thread="{{.ID}}"
     data-text-heading="Kommentare"
     data-text-form-heading="Kommentar schreiben"
     data-text-name="Name"
     data-text-email="E-Mail (optional, wird nie angezeigt)"
     data-text-message="Kommentar"
     data-text-submit="Absenden"
     data-text-pending="Danke! Dein Kommentar erscheint nach der Prüfung."></div>
```

| Attribute | Default |
|---|---|
| `data-text-heading` | Comments |
| `data-text-form-heading` | Leave a comment |
| `data-text-compose` | Write a comment (the link that opens the form) |
| `data-text-reply` | Reply |
| `data-text-replying-to` | Replying to |
| `data-text-cancel-reply` | Cancel reply |
| `data-text-loading` | Loading comments… |
| `data-text-load-error` | Comments could not be loaded. |
| `data-text-empty` | No comments yet. |
| `data-text-closed` | Comments are closed. |
| `data-text-name` | Name |
| `data-text-email` | Email (optional, never shown) |
| `data-text-message` | Comment |
| `data-text-submit` | Post comment |
| `data-text-sending` | Posting… |
| `data-text-published` | Thanks, your comment is published. |
| `data-text-pending` | Thanks! Your comment will appear once it has been reviewed. |
| `data-text-hint-plain` | Plain text. Line breaks are kept. |
| `data-text-hint-basic` | Plain text. Links, \*emphasis\* and \`code\` work; HTML is shown as typed. |
| `data-text-hint-basic-no-links` | Plain text. \*Emphasis\* and \`code\` work; HTML is shown as typed. |
| `data-text-honeypot` | Leave this field empty (label of the hidden spam trap) |
| `data-text-error-generic` | Your comment could not be sent. Please try again. |
| `data-text-error-network` | Your comment could not be sent. Check your connection and try again. |
| `data-text-error-too-fast` | That was quick! Please wait a few seconds and send again. |
| `data-text-error-rate-limited` | Too many comments in a short time. Please try again later. |
| `data-text-error-form-expired` | This form has expired. Please reload the page. |
| `data-text-error-name-required` | Please enter a name. |
| `data-text-error-body-required` | Please write a comment. |
| `data-text-error-email-invalid` | That email address does not look right. |
| `data-text-error-comments-closed` | Comments are closed. |
| `data-text-error-origin-not-allowed` | This site is not set up to post comments. |
| `data-text-error-reply-unavailable` | The comment you are replying to is no longer available. |
| `data-text-error-replies-off` | Replies are turned off here. |

With [pseudonyms](pseudonyms.md) turned on, these are used as well. `{name}`,
`{site}` and `{key}` are filled in where shown. An empty `data-text-unverified`
leaves the unverified mark out, but the naming policy still applies.

| Attribute | Default |
|---|---|
| `data-text-verified` | verified |
| `data-text-verified-title` | Only one reader holds the key to this name here, and they posted this. It says nothing about who they are. |
| `data-text-unverified` | unverified |
| `data-text-unverified-title` | This name is not a pseudonym. Anyone can post under it. |
| `data-text-name-policy` | Names marked “verified” are pseudonyms. Only their holder can post under them, or under a name that looks like them. Any other name is marked “unverified”. |
| `data-text-keep` | Keep this name as my pseudonym |
| `data-text-keep-help` | Your browser will hold a secret key so that only you can post under this name here. No account, no email. Comments under a pseudonym are publicly linked to each other, and this site still sees your network address as with any comment. Save the backup key you are shown next: without it, clearing your browser or changing device can cost you the name. |
| `data-text-kept` | {name} is now your pseudonym here. Save your backup key now. |
| `data-text-kept-no-storage` | {name} is now your pseudonym here, but this browser will not keep its key after you leave. Save your backup key now: you need it to post as {name} again. |
| `data-text-posting-as` | Posting as |
| `data-text-backup` | Backup key |
| `data-text-backup-help` | Anyone with this key can post as {name} here, so keep it private, for example in a password manager. Use it to restore your pseudonym on another device or after clearing your browser, and only on this site. |
| `data-text-backup-file` | The downloaded file's text, using {name}, {site} and {key}. Use `&#10;` for line breaks. |
| `data-text-copy` | Copy |
| `data-text-copied` | Copied. |
| `data-text-download` | Download |
| `data-text-forget` | Stop using it on this device |
| `data-text-forget-confirm` | Stop using {name} on this device? Without your backup key you cannot post as {name} again. |
| `data-text-restore` | Restore a pseudonym from a backup key |
| `data-text-restore-key` | Backup key |
| `data-text-restore-button` | Restore |
| `data-text-restored` | Welcome back. You are posting as {name}. |
| `data-text-error-pseudonym-taken` | That name, or one that looks very like it, is already someone’s pseudonym here. Please choose another. |
| `data-text-error-pseudonym-lost` | {name} is no longer held by your key here: the site’s owner released it and someone else has taken it since. Stop using it on this device to post under another name. |
| `data-text-error-name-reserved` | That name is too close to someone’s pseudonym here. Please choose a different name, or restore your backup key if the pseudonym is yours. |
| `data-text-error-name-marker` | Please leave “verified” and check marks out of your name; they mark verified names. |
| `data-text-error-pseudonym-name-invalid` | A pseudonym needs at least one letter or digit. |
| `data-text-error-pseudonym-key-invalid` | That backup key does not look right. |
| `data-text-error-pseudonym-unknown` | No pseudonym on this site uses that key. |
| `data-text-error-pseudonyms-off` | Pseudonyms are turned off here right now. Reload the page to post without one. |

Dates are formatted in the page's language (`<html lang="…">`).

## Structure

This is what the widget builds inside your container (one comment with one reply shown):

```html
<div data-afterword class="afterword afterword--ready">
  <h2 class="afterword-heading">Comments <span class="afterword-count">1</span></h2>
  <ol class="afterword-list">
    <li class="afterword-comment" id="comment-k3j2x9…">
      <p class="afterword-meta">
        <span class="afterword-author">Mara</span>
        <a class="afterword-permalink" href="#comment-k3j2x9…">
          <time class="afterword-date" datetime="2026-09-24T10:00:00.000Z">Sep 24, 2026</time>
        </a>
      </p>
      <div class="afterword-body">
        <p>Text with <em>emphasis</em>, <code>code</code><br>and
           <a href="https://…" rel="nofollow ugc noopener noreferrer">https://…</a></p>
      </div>
      <p class="afterword-comment-actions"><a class="afterword-reply-button" role="button" href="#afterword-form-1">Reply</a></p>
      <ol class="afterword-replies">
        <li class="afterword-comment afterword-comment--reply" id="comment-p7q…">
          <p class="afterword-meta">…</p>
          <p class="afterword-reply-to"><a class="afterword-reply-ref" href="#comment-k3j2x9…">@Mara · Sep 24, 2026, 10:00 AM</a></p>
          <div class="afterword-body">…</div>
          <p class="afterword-comment-actions"><a class="afterword-reply-button" role="button" href="#afterword-form-1">Reply</a></p>
        </li>
      </ol>
    </li>
  </ol>
  <p class="afterword-empty" hidden>No comments yet.</p>
  <!-- With pseudonyms on, the author line also carries one of:
       <span class="afterword-verified" title="…">verified</span>
       <span class="afterword-unverified" title="…">unverified</span>
       and a verified comment's <li> gets afterword-comment--verified. -->
  <p class="afterword-compose"><a class="afterword-compose-button" role="button"
     href="#afterword-form-1" aria-expanded="false">Write a comment</a></p>   <!-- not with data-form="open" -->
  <form class="afterword-form" id="afterword-form-1" hidden>   <!-- shown by the button or a Reply -->
    <h3 class="afterword-form-heading">Leave a comment</h3>
    <p class="afterword-replying" hidden>Replying to <a class="afterword-reply-ref" href="#comment-…">@Mara · …</a>
      <a class="afterword-cancel-reply" role="button" href="#">Cancel reply</a></p>
    <p class="afterword-field afterword-field--name">
      <label class="afterword-label">Name</label> <input class="afterword-input">
    </p>
    <div class="afterword-pseudonym">                <!-- only with pseudonyms on -->
      <p class="afterword-pseudonym-policy">Names marked “verified” are pseudonyms…</p>
      <p class="afterword-field afterword-field--keep">
        <input class="afterword-keep" type="checkbox"> <label class="afterword-keep-label">Keep this name…</label>
      </p>
      <p class="afterword-pseudonym-help" hidden>Your browser will hold a secret key…</p>
      <details class="afterword-restore"><summary>Restore a pseudonym…</summary>
        <p class="afterword-field afterword-field--restore">…</p>
        <p class="afterword-restore-actions"><a class="afterword-restore-button" role="button" href="#">Restore</a></p>
      </details>
    </div>
    <p class="afterword-field afterword-field--email">…</p>
    <p class="afterword-field afterword-field--message">
      <label class="afterword-label">Comment</label> <textarea class="afterword-textarea"></textarea>
    </p>
    <p class="afterword-hint">Plain text. Links, *emphasis* and `code` work…</p>
    <div class="afterword-hp" aria-hidden="true">…</div>
    <p class="afterword-actions"><button class="afterword-submit" type="submit">Post comment</button></p>
    <p class="afterword-notice afterword-notice--pending" role="status">Thanks! …</p>
  </form>
</div>
```

## Classes

| Class | Element |
|---|---|
| `afterword` | The container (added by the widget) |
| `afterword--loading`, `--ready`, `--error`, `--closed` | State of the container |
| `afterword-status` | "Loading…" / "could not be loaded" line |
| `afterword-heading` | The "Comments" heading |
| `afterword-count` | Number of comments inside the heading, replies included |
| `afterword-list` | `<ol>` of comments (hidden when empty) |
| `afterword-comment` | One `<li>`; its id is `comment-<id>`, so `:target` works |
| `afterword-meta` | Line with author and date |
| `afterword-author` | Commenter's name |
| `afterword-permalink` | Link to the comment |
| `afterword-date` | `<time>` with an ISO `datetime` and a longer `title` |
| `afterword-body` | Wrapper for the comment's paragraphs |
| `afterword-comment-actions`, `afterword-reply-button` | The “Reply” button under each comment (only when replies are on) |
| `afterword-replies` | `<ol>` of replies inside a top-level comment’s `<li>` (one level deep) |
| `afterword-comment--reply` | A reply’s `<li>` |
| `afterword-reply-to`, `afterword-reply-ref` | The “@Mara · date” line of a reply; a link when that comment is on the page |
| `afterword-empty` | "No comments yet." (hidden when there are comments) |
| `afterword-closed` | "Comments are closed." |
| `afterword-compose`, `afterword-compose-button` | “Write a comment”, which opens the collapsed form |
| `afterword-form` | The form (`hidden` until opened, unless `data-form="open"`) |
| `afterword-form-heading` | "Leave a comment" |
| `afterword-replying`, `afterword-cancel-reply` | “Replying to @Mara · date” and “Cancel reply” while answering a comment |
| `afterword-field` | Each label + control row |
| `afterword-field--name`, `--email`, `--message` | The specific row |
| `afterword-label` | Labels |
| `afterword-input` | Name and email inputs |
| `afterword-textarea` | The comment box |
| `afterword-hint` | Formatting hint under the comment box |
| `afterword-hp` | Hidden spam trap. Do not make it visible. |
| `afterword-actions` | Row containing the button |
| `afterword-submit` | The “Post comment” button, the only `<button>` |
| `afterword-notice` | Result message (`hidden` until there is one) |
| `afterword-notice--success`, `--pending`, `--error` | Kind of message |

Once a reader holds a pseudonym, the name row is hidden and the
`afterword-pseudonym` block (then also `afterword-pseudonym--held`) contains
instead:

```html
<p class="afterword-posting-as">Posting as <strong class="afterword-pseudonym-name">Mara</strong>
  <span class="afterword-verified">verified</span></p>
<details class="afterword-backup"><summary>Backup key</summary>
  <p class="afterword-backup-help">Anyone with this key…</p>
  <p class="afterword-field afterword-field--backup">… <input class="afterword-input afterword-backup-key" readonly></p>
  <p class="afterword-backup-actions"><a class="afterword-copy" role="button" href="#">Copy</a>
     <a class="afterword-download" download>Download</a> <span class="afterword-copied"></span></p>
</details>
<p class="afterword-pseudonym-actions"><a class="afterword-forget" role="button" href="#">Stop using it on this device</a></p>
```

| Pseudonym class | Element |
|---|---|
| `afterword-verified` | The “verified” mark on comments posted with the pseudonym’s key, and in “Posting as” |
| `afterword-unverified` | The mark on every other comment while pseudonyms are on |
| `afterword-comment--verified` | A comment `<li>` posted with the key |
| `afterword-pseudonym` | Block under the name field (`--held` once the reader holds one) |
| `afterword-pseudonym-policy` | The naming policy |
| `afterword-field--keep`, `afterword-keep`, `afterword-keep-label` | The “Keep this name” row, checkbox and label |
| `afterword-pseudonym-help` | What keeping a name means (shown when ticked) |
| `afterword-restore`, `afterword-field--restore`, `afterword-restore-button` | Restoring from a backup key |
| `afterword-posting-as`, `afterword-pseudonym-name` | “Posting as Mara” |
| `afterword-backup`, `afterword-backup-help`, `afterword-backup-key` | The backup key |
| `afterword-copy`, `afterword-download`, `afterword-copied` | Copy, download, and “Copied.” |
| `afterword-forget` | “Stop using it on this device” |

**Post comment** is the widget’s only `<button>`. Every other control (Write a
comment, Reply, Cancel reply, Restore, Copy, Stop using it on this device) is an
`<a role="button">`, so your theme’s link style applies to it and its button
style does not. They work with Enter and Space like buttons.

Invalid fields get `aria-invalid="true"`; the form gets `aria-busy="true"`
while sending.

## Styling in WriteFreely

WriteFreely's themes already style `input`, `textarea` and `button`, so the
form usually looks at home straight away. For adjustments, open your blog's
**Customize** page and add rules to **Custom CSS**, for example:

```css
.afterword { margin-top: 3em; border-top: 1px solid #eee; padding-top: 1em; }
.afterword-list { list-style: none; padding: 0; }
.afterword-comment { margin-bottom: 1.5em; }
.afterword-meta { font-family: var(--sans, sans-serif); font-size: 0.8em; color: #777; }
.afterword-author { font-weight: bold; color: inherit; }
.afterword-permalink { color: inherit; text-decoration: none; }
.afterword-input, .afterword-textarea { width: 100%; box-sizing: border-box; }
.afterword-notice--error { color: #c0392b; }
```

## Events

The container dispatches events you can listen for:

| Event | `detail` |
|---|---|
| `afterword:loaded` | `{ thread, count }` after the thread is shown |
| `afterword:posted` | `{ thread, status }` after a successful submission (`"published"` or `"pending"`) |

## If your blog sends a Content-Security-Policy

Allow the comment server for scripts and requests:

```
script-src 'self' https://comments.example.com;
connect-src 'self' https://comments.example.com;
```

Add it to `style-src` only if you link `afterword.css` from the comment server.
