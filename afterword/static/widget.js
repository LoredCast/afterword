/*! Afterword comment widget 1.3.0 — https://github.com/ (see README) — MIT */
/*
 * Usage (see docs/embedding.md):
 *   <div data-afterword data-thread="post-id"></div>
 *   <script src="https://comments.example.com/widget.js" defer></script>
 *
 * Security notes for reviewers:
 *   - Nothing received from the server is ever parsed as HTML. The widget only
 *     uses createElement, textContent and createTextNode.
 *   - Links are rebuilt from a URL string, allowed only for http(s) without
 *     credentials, and always get rel="nofollow ugc noopener noreferrer".
 *   - Requests never include cookies (credentials: "omit").
 *   - "Post comment" is the only <button>. Every other control is an <a> with
 *     role="button", so it looks like a link in any blog theme.
 *   - A pseudonym key (see docs/pseudonyms.md) is made here with
 *     crypto.getRandomValues, kept in this site's localStorage, and sent only
 *     when posting a comment or restoring a backup, never when loading comments.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var SCRIPT_BASE = script && script.src ? new URL("./", script.src).href : null;
  var instances = 0;
  var STORE = "afterword:pseudonym";
  var PENDING = "afterword:pseudonym-pending";   // a claim sent but not yet confirmed
  var KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

  var STRINGS = {
    heading: "Comments",
    formHeading: "Leave a comment",
    compose: "Write a comment",
    reply: "Reply",
    replyingTo: "Replying to",
    cancelReply: "Cancel reply",
    info: "What does this mean?",
    loading: "Loading comments…",
    loadError: "Comments could not be loaded.",
    empty: "No comments yet.",
    closed: "Comments are closed.",
    name: "Name",
    email: "Email (optional, never shown)",
    message: "Comment",
    submit: "Post comment",
    sending: "Posting…",
    published: "Thanks, your comment is published.",
    pending: "Thanks! Your comment will appear once it has been reviewed.",
    hintPlain: "Plain text. Line breaks are kept.",
    hintBasic: "Plain text. Links, *emphasis* and `code` work; HTML is shown as typed.",
    hintBasicNoLinks: "Plain text. *Emphasis* and `code` work; HTML is shown as typed.",
    honeypot: "Leave this field empty",
    errorGeneric: "Your comment could not be sent. Please try again.",
    errorNetwork: "Your comment could not be sent. Check your connection and try again.",
    errorTooFast: "That was quick! Please wait a few seconds and send again.",
    errorRateLimited: "Too many comments in a short time. Please try again later.",
    errorFormExpired: "This form has expired. Please reload the page.",
    errorNameRequired: "Please enter a name.",
    errorBodyRequired: "Please write a comment.",
    errorEmailInvalid: "That email address does not look right.",
    errorCommentsClosed: "Comments are closed.",
    errorOriginNotAllowed: "This site is not set up to post comments.",
    errorReplyUnavailable: "The comment you are replying to is no longer available.",
    errorRepliesOff: "Replies are turned off here.",
    verified: "verified",
    verifiedTitle: "Only one reader holds the key to this name here, and they posted this. It says nothing about who they are.",
    unverified: "unverified",
    unverifiedTitle: "This name is not a pseudonym. Anyone can post under it.",
    namePolicy: "Names marked \u201cverified\u201d are pseudonyms. Only their holder can post under them, or under a name that looks like them. Any other name is marked \u201cunverified\u201d.",
    keep: "Keep this name as my pseudonym",
    keepHelp: "Your browser will hold a secret key so that only you can post under this name here. No account, no email. Comments under a pseudonym are publicly linked to each other, and this site still sees your network address as with any comment. Save the backup key you are shown next: without it, clearing your browser or changing device can cost you the name.",
    kept: "{name} is now your pseudonym here. Save your backup key now.",
    keptNoStorage: "{name} is now your pseudonym here, but this browser will not keep its key after you leave. Save your backup key now: you need it to post as {name} again.",
    postingAs: "Posting as",
    backup: "Backup key",
    backupHelp: "Anyone with this key can post as {name} here, so keep it private, for example in a password manager. Use it to restore your pseudonym on another device or after clearing your browser, and only on this site.",
    backupFile: "Afterword pseudonym backup\n\nName: {name}\nSite: {site}\nKey:  {key}\n\nAnyone with this key can post as {name} on this site. Keep it private.\nTo use it on another device, open a post on {site}, choose\n\u201cRestore a pseudonym from a backup key\u201d under the comment form, and paste the key.\n",
    copy: "Copy",
    copied: "Copied.",
    download: "Download",
    forget: "Stop using it on this device",
    forgetConfirm: "Stop using {name} on this device? Without your backup key you cannot post as {name} again.",
    restore: "Restore a pseudonym from a backup key",
    restoreKey: "Backup key",
    restoreButton: "Restore",
    restored: "Welcome back. You are posting as {name}.",
    errorPseudonymTaken: "That name, or one that looks very like it, is already someone\u2019s pseudonym here. Please choose another.",
    errorPseudonymLost: "{name} is no longer held by your key here: the site\u2019s owner released it and someone else has taken it since. Stop using it on this device to post under another name.",
    errorNameReserved: "That name is too close to someone\u2019s pseudonym here. Please choose a different name, or restore your backup key if the pseudonym is yours.",
    errorNameMarker: "Please leave \u201cverified\u201d and check marks out of your name; they mark verified names.",
    errorPseudonymNameInvalid: "A pseudonym needs at least one letter or digit.",
    errorPseudonymKeyInvalid: "That backup key does not look right.",
    errorPseudonymUnknown: "No pseudonym on this site uses that key.",
    errorPseudonymsOff: "Pseudonyms are turned off here right now. Reload the page to post without one."
  };

  function camel(code) {
    return code.replace(/_([a-z])/g, function (_, c) { return c.toUpperCase(); });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // A link that acts like a button: it takes the blog's link style, not its
  // button style. href points at what it opens when there is such a place.
  function action(className, text, handler, href) {
    var link = el("a", className, text);
    link.href = href || "#";
    link.setAttribute("role", "button");
    link.addEventListener("click", function (event) {
      event.preventDefault();
      handler(event);
    });
    link.addEventListener("keydown", function (event) {
      if (event.key === " ") {           // Enter already clicks a link; Space should too
        event.preventDefault();
        handler(event);
      }
    });
    return link;
  }

  // A small "?" that keeps an explanation out of the way: hovering shows it as
  // the browser's own tooltip (no CSS needed), and a click, tap, Enter or Space
  // shows it inline for touch and keyboard users. Escape or leaving hides it.
  function info(id, text, label) {
    var box = el("span", "afterword-info");
    var tip = el("span", "afterword-info-text", text);
    tip.id = id;
    tip.hidden = true;
    var mark = action("afterword-info-mark", "?", function () { show(tip.hidden); });
    function show(on) {
      tip.hidden = !on;
      mark.setAttribute("aria-expanded", on ? "true" : "false");
    }
    mark.title = text;
    mark.setAttribute("aria-label", label);
    mark.setAttribute("aria-expanded", "false");
    mark.setAttribute("aria-controls", id);
    mark.addEventListener("keydown", function (event) {
      if (event.key === "Escape") show(false);
    });
    mark.addEventListener("blur", function () { show(false); });
    box.appendChild(mark);
    box.appendChild(document.createTextNode(" "));
    box.appendChild(tip);
    return box;
  }

  function formatDate(date, withTime) {
    var lang = document.documentElement.lang || undefined;
    try {
      return new Intl.DateTimeFormat(lang, withTime ? { dateStyle: "medium", timeStyle: "short" }
                                                     : { dateStyle: "medium" }).format(date);
    } catch (e) {
      return withTime ? date.toLocaleString() : date.toDateString();
    }
  }

  function commentId(value) {
    return typeof value === "string" && /^[a-z0-9]{1,32}$/.test(value) ? value : null;
  }

  function fill(text, values) {
    return String(text).replace(/\{(\w+)\}/g, function (all, name) {
      return values && values[name] !== undefined ? String(values[name]) : all;
    });
  }

  // -- pseudonym keys: 32 base32 characters, 160 random bits ------------------
  function canMakeKeys() {
    return !!(window.crypto && window.crypto.getRandomValues && window.Uint8Array);
  }

  function newKey() {
    var bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    var key = "";
    for (var i = 0; i < bytes.length; i++) key += KEY_ALPHABET.charAt(bytes[i] & 31);
    return key;
  }

  function formatKey(key) { return key.match(/.{4}/g).join("-"); }

  // Accepts the key with or without dashes, or a whole pasted backup file.
  function parseKey(text) {
    var found = /(?:[a-z2-7]{4}-){7}[a-z2-7]{4}/i.exec(String(text));
    var key = (found ? found[0] : String(text)).toLowerCase().replace(/[\s-]/g, "");
    return /^[a-z2-7]{32}$/.test(key) ? key : null;
  }

  function loadPseudonym(slot) {
    var value = null;
    try { value = JSON.parse(localStorage.getItem(slot) || "null"); } catch (e) { return null; }
    if (!value || typeof value.name !== "string" || !value.name || !parseKey(value.key)) return null;
    return { name: value.name, key: parseKey(value.key) };
  }

  function savePseudonym(slot, value) {
    try {
      if (value) localStorage.setItem(slot, JSON.stringify(value));
      else localStorage.removeItem(slot);
      return true;
    } catch (e) {
      return false;
    }
  }

  function safeHref(value) {
    try {
      var url = new URL(String(value));
      if ((url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password) {
        return url.href;
      }
    } catch (e) { /* not a URL */ }
    return null;
  }

  function Widget(container) {
    this.root = container;
    this.n = ++instances;
    var data = container.dataset;
    this.strings = {};
    for (var key in STRINGS) {
      var override = data["text" + key.charAt(0).toUpperCase() + key.slice(1)];
      this.strings[key] = override !== undefined ? override : STRINGS[key];
    }
    var server = data.server || SCRIPT_BASE;
    this.base = server ? new URL(server.replace(/\/?$/, "/"), location.href).href : null;
    this.thread = this.threadId();
    this.level = Math.min(6, Math.max(2, parseInt(data.headingLevel || "2", 10) || 2));
    this.remember = data.remember === "true";
    this.token = null;
    this.busy = false;
  }

  Widget.prototype.t = function (key) { return this.strings[key]; };

  Widget.prototype.threadId = function () {
    var data = this.root.dataset;
    if (data.thread) return data.thread.trim();
    var path = location.pathname;
    if (data.threadFrom === "canonical") {
      var link = document.querySelector('link[rel="canonical"]');
      if (link && link.href) {
        try { path = new URL(link.href, location.href).pathname; } catch (e) { /* keep path */ }
      }
    }
    if (path.length > 1) path = path.replace(/\/+$/, "");
    return path || "/";
  };

  Widget.prototype.api = function (path) { return new URL(path, this.base).href; };

  Widget.prototype.setState = function (state) {
    var cls = this.root.classList;
    ["loading", "ready", "error", "closed"].forEach(function (s) { cls.remove("afterword--" + s); });
    cls.add("afterword--" + state);
  };

  Widget.prototype.emit = function (name, detail) {
    var event;
    try {
      event = new CustomEvent("afterword:" + name, { bubbles: true, detail: detail });
    } catch (e) {
      return;
    }
    this.root.dispatchEvent(event);
  };

  Widget.prototype.start = function () {
    var self = this;
    this.root.classList.add("afterword");
    this.root.textContent = "";
    this.status = el("p", "afterword-status", this.t("loading"));
    this.status.setAttribute("role", "status");
    this.root.appendChild(this.status);
    this.setState("loading");
    if (!this.base) {
      this.fail();
      return;
    }
    fetch(this.api("api/v1/thread?id=" + encodeURIComponent(this.thread)), {
      credentials: "omit", mode: "cors", headers: { Accept: "application/json" }
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }).then(function (data) {
      self.render(data);
    }).catch(function () {
      self.fail();
    });
  };

  Widget.prototype.fail = function () {
    this.status.textContent = this.t("loadError");
    this.setState("error");
  };

  Widget.prototype.render = function (data) {
    var comments = Array.isArray(data.comments) ? data.comments : [];
    this.order = data.order === "newest" ? "newest" : "oldest";
    this.pseudonyms = data.pseudonyms === true;
    this.replies = !!(data.open && data.form && data.form.replies === true);
    this.root.textContent = "";
    if (this.t("heading")) {
      var heading = el("h" + this.level, "afterword-heading", this.t("heading") + " ");
      this.count = el("span", "afterword-count", comments.length);
      heading.appendChild(this.count);
      this.root.appendChild(heading);
    }
    this.list = el("ol", "afterword-list");
    this.emptyNote = el("p", "afterword-empty", this.t("empty"));
    for (var i = 0; i < comments.length; i++) {
      var item = this.comment(comments[i], false);
      if (item) this.list.appendChild(item);
    }
    this.root.appendChild(this.list);
    this.root.appendChild(this.emptyNote);
    this.syncEmpty();

    if (data.open && data.form) {
      this.token = data.form.token;
      // The form folds away under "Write a comment" (open and close it like
      // "Restore a pseudonym"), unless the page asks for it open: data-form="open".
      this.collapsible = this.root.dataset.form !== "open";
      this.formEl = this.form(data.form);
      if (this.collapsible) {
        var self = this;
        this.composeEl = el("details", "afterword-compose");
        this.composeEl.appendChild(el("summary", "afterword-compose-summary", this.t("compose")));
        this.composeEl.appendChild(this.formEl);
        this.composeEl.addEventListener("toggle", function () {
          if (!self.composeEl.open) self.cancelReply();       // closing it drops a reply in progress
        });
        this.root.appendChild(this.composeEl);
      } else {
        this.root.appendChild(this.formEl);
      }
      this.setState("ready");
    } else {
      this.root.appendChild(el("p", "afterword-closed", this.t("closed")));
      this.setState("closed");
    }
    this.emit("loaded", { thread: this.thread, count: this.list.querySelectorAll(".afterword-comment").length });
    this.scrollToHash();
  };

  Widget.prototype.syncEmpty = function () {
    var n = this.list.children.length;
    this.list.hidden = n === 0;
    this.emptyNote.hidden = n !== 0;
    if (this.count) this.count.textContent = String(this.list.querySelectorAll(".afterword-comment").length);
  };

  Widget.prototype.openForm = function (focus) {
    if (!this.formEl) return;
    if (this.composeEl) this.composeEl.open = true;
    var target = focus || (this.nameInput && !this.nameInput.closest("[hidden]") && !this.nameInput.value
      ? this.nameInput : this.messageInput);
    if (target && target.focus) target.focus();
  };

  // Replies are one level deep: the server files a reply to a reply under the
  // same top-level comment, and the reference line says which one it answers.
  Widget.prototype.startReply = function (c, id) {
    this.target = { id: id, author: String(c.author), created: String(c.created) };
    var line = this.replying;
    line.textContent = "";
    line.appendChild(document.createTextNode(this.t("replyingTo") + " "));
    line.appendChild(this.reference(this.target));
    line.appendChild(document.createTextNode(" "));
    var self = this;
    var cancel = action("afterword-cancel-reply", this.t("cancelReply"), function () {
      self.cancelReply();
      if (self.messageInput) self.messageInput.focus();
    });
    line.appendChild(cancel);
    line.hidden = false;
    this.openForm(this.messageInput);
    if (this.formEl.scrollIntoView) this.formEl.scrollIntoView({ block: "nearest" });
  };

  Widget.prototype.cancelReply = function () {
    this.target = null;
    if (this.replying) {
      this.replying.hidden = true;
      this.replying.textContent = "";
    }
  };

  // "@Mara · 24 Sep 2026, 10:00", linked to that comment when it is on the page.
  Widget.prototype.reference = function (ref) {
    var date = new Date(String(ref.created));
    var label = "@" + String(ref.author || "") + (isNaN(date.getTime()) ? "" : " \u00b7 " + formatDate(date, true));
    var id = commentId(ref.id);
    if (!id) return el("span", "afterword-reply-ref", label);
    var link = el("a", "afterword-reply-ref", label);
    link.href = "#comment-" + id;
    return link;
  };

  Widget.prototype.scrollToHash = function () {
    var hash = location.hash.slice(1);
    if (!/^comment-[a-z0-9]{1,32}$/.test(hash)) return;
    var target = document.getElementById(hash);
    if (target && target.scrollIntoView) target.scrollIntoView();
  };

  Widget.prototype.comment = function (c, isReply) {
    if (!c || typeof c !== "object") return null;
    var self = this;
    var id = commentId(c.id);
    var item = el("li", "afterword-comment" + (isReply ? " afterword-comment--reply" : ""));
    if (id) item.id = "comment-" + id;

    var meta = el("p", "afterword-meta");
    meta.appendChild(el("span", "afterword-author", c.author));
    meta.appendChild(document.createTextNode(" "));
    // Only an explicit true counts: the server sets it when the comment was sent with the key.
    var badge = c.verified === true ? this.badge(true) : this.pseudonyms ? this.badge(false) : null;
    if (c.verified === true) item.classList.add("afterword-comment--verified");
    if (badge) {
      meta.appendChild(badge);
      meta.appendChild(document.createTextNode(" "));
    }
    var date = new Date(String(c.created));
    var time = el("time", "afterword-date");
    if (!isNaN(date.getTime())) {
      time.dateTime = date.toISOString();
      time.textContent = formatDate(date, false);
      time.title = formatDate(date, true);
    }
    if (id) {
      var permalink = el("a", "afterword-permalink");
      permalink.href = "#comment-" + id;
      permalink.appendChild(time);
      meta.appendChild(permalink);
    } else {
      meta.appendChild(time);
    }
    item.appendChild(meta);
    if (c.reply_to && typeof c.reply_to === "object") {
      var ref = el("p", "afterword-reply-to");
      ref.appendChild(this.reference(c.reply_to));
      item.appendChild(ref);
    }
    item.appendChild(this.body(c));
    if (this.replies && id) {
      var reply = action("afterword-reply-button", this.t("reply"),
                         function () { self.startReply(c, id); }, "#afterword-form-" + this.n);
      var actions = el("p", "afterword-comment-actions");
      actions.appendChild(reply);
      item.appendChild(actions);
    }
    if (!isReply && Array.isArray(c.replies) && c.replies.length) {
      var list = el("ol", "afterword-replies");
      for (var i = 0; i < c.replies.length; i++) {
        var child = this.comment(c.replies[i], true);
        if (child) list.appendChild(child);
      }
      item.appendChild(list);
    }
    return item;
  };

  // Where a newly published comment goes: under its top-level comment if that is shown.
  Widget.prototype.place = function (c, item) {
    var parentId = commentId(c.parent);
    var children = this.list.children;
    for (var i = 0; parentId && i < children.length; i++) {
      if (children[i].id !== "comment-" + parentId) continue;
      var replies = null;
      for (var j = 0; j < children[i].children.length; j++) {
        if (children[i].children[j].classList.contains("afterword-replies")) replies = children[i].children[j];
      }
      if (!replies) replies = children[i].appendChild(el("ol", "afterword-replies"));
      item.classList.add("afterword-comment--reply");
      replies.appendChild(item);
      return;
    }
    var newestFirst = this.list.firstChild && this.order === "newest";
    this.list.insertBefore(item, newestFirst ? this.list.firstChild : null);
  };

  Widget.prototype.badge = function (verified) {
    var text = this.t(verified ? "verified" : "unverified");
    if (!text) return null;
    var badge = el("span", verified ? "afterword-verified" : "afterword-unverified", text);
    var title = this.t(verified ? "verifiedTitle" : "unverifiedTitle");
    if (title) badge.title = title;
    return badge;
  };

  Widget.prototype.errorText = function (data) {
    var key = data && data.error ? "error" + camel("_" + data.error) : "";
    return (key && this.strings[key]) || (data && data.message) || this.t("errorGeneric");
  };

  Widget.prototype.body = function (c) {
    var wrap = el("div", "afterword-body");
    var paragraphs = Array.isArray(c.body) ? c.body : null;
    if (!paragraphs) {
      // Fallback: plain text, split into paragraphs.
      String(c.text || "").split(/\n{2,}/).forEach(function (block) {
        var p = el("p");
        block.split("\n").forEach(function (line, i) {
          if (i) p.appendChild(document.createElement("br"));
          p.appendChild(document.createTextNode(line));
        });
        wrap.appendChild(p);
      });
      return wrap;
    }
    paragraphs.forEach(function (segments) {
      var p = el("p");
      (Array.isArray(segments) ? segments : []).forEach(function (s) {
        if (!s || typeof s !== "object") return;
        if (s.t === "br") {
          p.appendChild(document.createElement("br"));
        } else if (s.t === "em") {
          p.appendChild(el("em", null, s.v));
        } else if (s.t === "code") {
          p.appendChild(el("code", null, s.v));
        } else if (s.t === "a" && safeHref(s.v)) {
          var a = el("a", null, s.v);
          a.href = safeHref(s.v);
          a.rel = "nofollow ugc noopener noreferrer";
          p.appendChild(a);
        } else if (s.v !== undefined && s.v !== null) {
          p.appendChild(document.createTextNode(String(s.v)));
        }
      });
      wrap.appendChild(p);
    });
    return wrap;
  };

  // A label and its control, with an optional "?" explanation between them.
  Widget.prototype.field = function (kind, label, control, explanation) {
    var id = "afterword-" + kind + "-" + this.n;
    var row = el("p", "afterword-field afterword-field--" + kind);
    var lab = el("label", "afterword-label", label);
    lab.htmlFor = id;
    control.id = id;
    row.appendChild(lab);
    if (explanation) {
      row.appendChild(document.createTextNode(" "));
      row.appendChild(explanation);
      row.appendChild(document.createTextNode(" "));
    }
    row.appendChild(control);
    return row;
  };

  // The pseudonym controls under the name field. Returns what the form needs:
  // current() -> {name, key} or null, keyFor(name) -> key to send or null,
  // and posted(data) after a successful submission.
  Widget.prototype.pseudonymPart = function (nameRow, name) {
    var self = this;
    var n = this.n;
    var current = loadPseudonym(STORE);
    var claiming = null;
    var keep = null;
    var box = el("div", "afterword-pseudonym");

    function t(key, values) { return fill(self.t(key), values); }

    function notice(kind, text) {
      var p = el("p", "afterword-notice afterword-notice--" + kind, text);
      p.setAttribute("role", kind === "error" ? "alert" : "status");
      return p;
    }

    function wrap(tag, className, children) {
      var node = el(tag, className);
      children.forEach(function (child) {
        node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
      });
      return node;
    }

    function drawHeld(message, showBackup) {
      name.value = current.name;
      var line = wrap("p", "afterword-posting-as", [t("postingAs") + " ",
        el("strong", "afterword-pseudonym-name", current.name), " "]);
      var badge = self.badge(true);
      if (badge) line.appendChild(badge);
      box.appendChild(line);
      if (message) box.appendChild(notice("success", message));

      var backup = el("details", "afterword-backup");
      backup.appendChild(el("summary", null, t("backup")));
      var field = el("input", "afterword-input afterword-backup-key");
      field.type = "text";
      field.readOnly = true;
      field.spellcheck = false;
      field.autocomplete = "off";
      field.value = formatKey(current.key);
      backup.appendChild(self.field("backup", t("backup"), field,
        info("afterword-backup-help-" + n, t("backupHelp", { name: current.name }), t("info"))));
      var copied = el("span", "afterword-copied");
      copied.setAttribute("role", "status");
      var copy = action("afterword-copy", t("copy"), function () {
        function done() { copied.textContent = " " + t("copied"); }
        field.focus();
        field.select();
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(field.value).then(done, function () { /* selected; copy by hand */ });
        } else {
          try { if (document.execCommand("copy")) done(); } catch (e) { /* selected; copy by hand */ }
        }
      });
      var download = el("a", "afterword-download", t("download"));
      download.href = "data:text/plain;charset=utf-8," + encodeURIComponent(
        t("backupFile", { name: current.name, site: location.origin, key: formatKey(current.key) }));
      download.setAttribute("download", "afterword-pseudonym.txt");
      backup.appendChild(wrap("p", "afterword-backup-actions", [copy, " ", download, copied]));
      backup.open = !!showBackup;
      box.appendChild(backup);

      var forget = action("afterword-forget", t("forget"), function () {
        if (!window.confirm(t("forgetConfirm", { name: current.name }))) return;
        savePseudonym(STORE, null);
        current = null;
        name.value = "";
        draw();
        name.focus();
      });
      box.appendChild(wrap("p", "afterword-pseudonym-actions", [forget]));
    }

    function drawFree() {
      if (!canMakeKeys()) return;
      keep = el("input", "afterword-keep");
      keep.type = "checkbox";
      keep.name = "keep";
      keep.id = "afterword-keep-" + n;
      var label = el("label", "afterword-keep-label", t("keep"));
      label.htmlFor = keep.id;
      var helpId = "afterword-keep-help-" + n;
      keep.setAttribute("aria-describedby", helpId);
      box.appendChild(wrap("p", "afterword-field afterword-field--keep",
                           [keep, " ", label, " ", info(helpId, t("keepHelp"), t("info"))]));

      var restore = el("details", "afterword-restore");
      restore.appendChild(el("summary", null, t("restore")));
      var input = el("input", "afterword-input");
      input.type = "text";
      input.name = "restore";
      input.spellcheck = false;
      input.autocomplete = "off";
      restore.appendChild(self.field("restore", t("restoreKey"), input));
      var busy = false;
      var button = action("afterword-restore-button", t("restoreButton"), function () { send(); });
      restore.appendChild(wrap("p", "afterword-restore-actions", [button]));
      var result = null;
      function report(text) {
        if (result) restore.removeChild(result);
        result = restore.appendChild(notice("error", text));
      }
      function send() {
        if (busy) return;
        var key = parseKey(input.value);
        input.removeAttribute("aria-invalid");
        if (!key) {
          input.setAttribute("aria-invalid", "true");
          return report(t("errorPseudonymKeyInvalid"));
        }
        busy = true;
        button.setAttribute("aria-disabled", "true");
        fetch(self.api("api/v1/pseudonym"), {
          method: "POST", credentials: "omit", mode: "cors",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ key: key })
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            return { status: resp.status, data: data || {} };
          });
        }).then(function (reply) {
          var held = reply.data.pseudonym;
          if (reply.status === 200 && held && typeof held.name === "string" && held.name) {
            current = { name: held.name, key: key };
            var stored = savePseudonym(STORE, current);
            savePseudonym(PENDING, null);
            draw(t(stored ? "restored" : "keptNoStorage", { name: current.name }), !stored);
          } else {
            report(self.errorText(reply.data));
          }
        }).catch(function () {
          report(t("errorNetwork"));
        }).then(function () {
          busy = false;
          button.removeAttribute("aria-disabled");
        });
      }
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();   // do not send the comment form
          send();
        }
      });
      box.appendChild(restore);
    }

    function draw(message, showBackup) {
      box.textContent = "";
      keep = null;
      nameRow.hidden = !!current;
      box.className = "afterword-pseudonym" + (current ? " afterword-pseudonym--held" : "");
      if (current) drawHeld(message, showBackup);
      else drawFree();
    }

    draw();
    return {
      root: box,
      current: function () { return current; },
      keyFor: function (author) {
        claiming = null;
        if (current) return current.key;
        if (!keep || !keep.checked) return null;
        // Reuse the key of an unconfirmed claim for the same name: if that answer
        // was lost on the way back, the server already holds the name for it.
        var pending = loadPseudonym(PENDING);
        claiming = { name: author, key: pending && pending.name === author ? pending.key : newKey() };
        savePseudonym(PENDING, claiming);
        return claiming.key;
      },
      posted: function (data) {
        var held = data && data.pseudonym;
        if (current || !claiming || !held || typeof held.name !== "string" || !held.name) return;
        current = { name: held.name, key: claiming.key };
        var stored = savePseudonym(STORE, current);
        savePseudonym(PENDING, null);
        draw(t(stored ? "kept" : "keptNoStorage", { name: current.name }), true);
      }
    };
  };

  Widget.prototype.form = function (cfg) {
    var self = this;
    var form = el("form", "afterword-form");
    form.id = "afterword-form-" + this.n;
    form.setAttribute("novalidate", "");
    if (!this.collapsible && this.t("formHeading")) {
      form.appendChild(el("h" + Math.min(6, this.level + 1), "afterword-form-heading", this.t("formHeading")));
    }
    this.replying = form.appendChild(el("p", "afterword-replying"));
    this.replying.setAttribute("aria-live", "polite");
    this.replying.hidden = true;

    var name = el("input", "afterword-input");
    name.name = "author";
    name.type = "text";
    name.required = true;
    name.maxLength = cfg.maxName || 80;
    name.autocomplete = "name";
    var policy = null;
    if (this.pseudonyms) {
      policy = info("afterword-policy-" + this.n, this.t("namePolicy"), this.t("info"));
      name.setAttribute("aria-describedby", "afterword-policy-" + this.n);
    }
    var nameRow = form.appendChild(this.field("name", this.t("name"), name, policy));
    var pseudonym = this.pseudonyms ? this.pseudonymPart(nameRow, name) : null;
    if (pseudonym) form.appendChild(pseudonym.root);

    var email = null;
    if (cfg.email) {
      email = el("input", "afterword-input");
      email.name = "email";
      email.type = "email";
      email.maxLength = 254;
      email.autocomplete = "email";
      form.appendChild(this.field("email", this.t("email"), email));
    }

    var message = el("textarea", "afterword-textarea");
    message.name = "body";
    message.required = true;
    message.rows = 6;
    this.messageInput = message;
    this.nameInput = name;
    message.maxLength = cfg.maxBody || 5000;
    var hintId = "afterword-hint-" + this.n;
    message.setAttribute("aria-describedby", hintId);
    var hintKey = cfg.formatting === "basic" ? (cfg.links ? "hintBasic" : "hintBasicNoLinks") : "hintPlain";
    form.appendChild(this.field("message", this.t("message"), message,
                                info(hintId, this.t(hintKey), this.t("info"))));

    // Honeypot: hidden from people (and from assistive technology), tempting to bots.
    var trap = el("div", "afterword-hp");
    trap.setAttribute("aria-hidden", "true");
    trap.style.position = "absolute";
    trap.style.left = "-10000px";
    trap.style.width = "1px";
    trap.style.height = "1px";
    trap.style.overflow = "hidden";
    var trapInput = el("input");
    trapInput.type = "text";
    trapInput.name = "website";
    trapInput.tabIndex = -1;
    trapInput.autocomplete = "off";
    var trapLabel = el("label", null, this.t("honeypot"));
    trapLabel.appendChild(trapInput);
    trap.appendChild(trapLabel);
    form.appendChild(trap);

    var actions = el("p", "afterword-actions");
    var button = el("button", "afterword-submit", this.t("submit"));
    button.type = "submit";
    actions.appendChild(button);
    form.appendChild(actions);

    var notice = el("p", "afterword-notice");
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.hidden = true;
    form.appendChild(notice);

    if (this.remember) {
      try {
        var saved = JSON.parse(localStorage.getItem("afterword:identity") || "{}");
        if (typeof saved.name === "string" && !(pseudonym && pseudonym.current())) name.value = saved.name;
        if (email && typeof saved.email === "string") email.value = saved.email;
      } catch (e) { /* storage unavailable */ }
    }

    function show(kind, text) {
      notice.className = "afterword-notice afterword-notice--" + kind;
      notice.textContent = text;
      notice.hidden = false;
    }

    function invalid(control, key) {
      control.setAttribute("aria-invalid", "true");
      show("error", self.t(key));
      control.focus();
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (self.busy) return;
      [name, email, message].forEach(function (c) { if (c) c.removeAttribute("aria-invalid"); });
      var held = pseudonym && pseudonym.current();
      if (!held && !name.value.trim()) return invalid(name, "errorNameRequired");
      if (email && email.value.trim() && !email.checkValidity()) return invalid(email, "errorEmailInvalid");
      if (!message.value.trim()) return invalid(message, "errorBodyRequired");

      self.busy = true;
      button.disabled = true;
      button.textContent = self.t("sending");
      form.setAttribute("aria-busy", "true");
      var payload = {
        thread: self.thread,
        page: location.origin + location.pathname,
        author: held ? held.name : name.value,
        email: email ? email.value : "",
        body: message.value,
        token: self.token,
        website: trapInput.value
      };
      var key = pseudonym ? pseudonym.keyFor(payload.author) : null;
      if (key) payload.key = key;
      if (self.target) payload.reply_to = self.target.id;
      fetch(self.api("api/v1/comments"), {
        method: "POST", credentials: "omit", mode: "cors",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload)
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (data) {
          return { status: resp.status, data: data || {} };
        });
      }).then(function (result) {
        var data = result.data;
        if (data.token) self.token = data.token;
        if (result.status === 201 || result.status === 202) {
          message.value = "";
          if (pseudonym) pseudonym.posted(data);
          if (self.remember) {
            try {
              localStorage.setItem("afterword:identity",
                JSON.stringify({ name: name.value, email: email ? email.value : "" }));
            } catch (e) { /* storage unavailable */ }
          }
          if (data.status === "published" && data.comment) {
            var item = self.comment(data.comment, false);
            if (item) self.place(data.comment, item);
            self.syncEmpty();
            show("success", self.t("published"));
          } else {
            show("pending", self.t("pending"));
          }
          self.cancelReply();
          self.emit("posted", { thread: self.thread, status: data.status || "pending" });
        } else {
          show("error", held && data.error === "pseudonym_taken"
            ? fill(self.t("errorPseudonymLost"), { name: held.name }) : self.errorText(data));
          if (data.error === "reply_unavailable") self.cancelReply();
          if (data.field === "author") name.setAttribute("aria-invalid", "true");
          if (data.field === "email" && email) email.setAttribute("aria-invalid", "true");
          if (data.field === "body") message.setAttribute("aria-invalid", "true");
        }
      }).catch(function () {
        show("error", self.t("errorNetwork"));
      }).then(function () {
        self.busy = false;
        button.disabled = false;
        button.textContent = self.t("submit");
        form.removeAttribute("aria-busy");
      });
    });
    return form;
  };

  function init(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll("[data-afterword]:not(.afterword)");
    for (var i = 0; i < nodes.length; i++) new Widget(nodes[i]).start();
  }

  window.Afterword = { init: init, version: "1.3.0" };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(); });
  } else {
    init();
  }
})();
