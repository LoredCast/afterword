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

Dates are formatted in the page's language (`<html lang="…">`).

## Structure

This is what the widget builds inside your container (one comment shown):

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
    </li>
  </ol>
  <p class="afterword-empty" hidden>No comments yet.</p>
  <form class="afterword-form">
    <h3 class="afterword-form-heading">Leave a comment</h3>
    <p class="afterword-field afterword-field--name">
      <label class="afterword-label">Name</label> <input class="afterword-input">
    </p>
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
| `afterword-count` | Number of comments inside the heading |
| `afterword-list` | `<ol>` of comments (hidden when empty) |
| `afterword-comment` | One `<li>`; its id is `comment-<id>`, so `:target` works |
| `afterword-meta` | Line with author and date |
| `afterword-author` | Commenter's name |
| `afterword-permalink` | Link to the comment |
| `afterword-date` | `<time>` with an ISO `datetime` and a longer `title` |
| `afterword-body` | Wrapper for the comment's paragraphs |
| `afterword-empty` | "No comments yet." (hidden when there are comments) |
| `afterword-closed` | "Comments are closed." |
| `afterword-form` | The form |
| `afterword-form-heading` | "Leave a comment" |
| `afterword-field` | Each label + control row |
| `afterword-field--name`, `--email`, `--message` | The specific row |
| `afterword-label` | Labels |
| `afterword-input` | Name and email inputs |
| `afterword-textarea` | The comment box |
| `afterword-hint` | Formatting hint under the comment box |
| `afterword-hp` | Hidden spam trap. Do not make it visible. |
| `afterword-actions` | Row containing the button |
| `afterword-submit` | The button |
| `afterword-notice` | Result message (`hidden` until there is one) |
| `afterword-notice--success`, `--pending`, `--error` | Kind of message |

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
