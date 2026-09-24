/*! Afterword comment widget 1.0.0 — https://github.com/ (see README) — MIT */
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
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var SCRIPT_BASE = script && script.src ? new URL("./", script.src).href : null;
  var instances = 0;

  var STRINGS = {
    heading: "Comments",
    formHeading: "Leave a comment",
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
    errorOriginNotAllowed: "This site is not set up to post comments."
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
      var item = this.comment(comments[i]);
      if (item) this.list.appendChild(item);
    }
    this.root.appendChild(this.list);
    this.root.appendChild(this.emptyNote);
    this.syncEmpty();

    if (data.open && data.form) {
      this.token = data.form.token;
      this.root.appendChild(this.form(data.form));
      this.setState("ready");
    } else {
      this.root.appendChild(el("p", "afterword-closed", this.t("closed")));
      this.setState("closed");
    }
    this.emit("loaded", { thread: this.thread, count: comments.length });
    this.scrollToHash();
  };

  Widget.prototype.syncEmpty = function () {
    var n = this.list.children.length;
    this.list.hidden = n === 0;
    this.emptyNote.hidden = n !== 0;
    if (this.count) this.count.textContent = String(n);
  };

  Widget.prototype.scrollToHash = function () {
    var hash = location.hash.slice(1);
    if (!/^comment-[a-z0-9]{1,32}$/.test(hash)) return;
    var target = document.getElementById(hash);
    if (target && target.scrollIntoView) target.scrollIntoView();
  };

  Widget.prototype.comment = function (c) {
    if (!c || typeof c !== "object") return null;
    var id = /^[a-z0-9]{1,32}$/.test(String(c.id)) ? String(c.id) : null;
    var item = el("li", "afterword-comment");
    if (id) item.id = "comment-" + id;

    var meta = el("p", "afterword-meta");
    meta.appendChild(el("span", "afterword-author", c.author));
    meta.appendChild(document.createTextNode(" "));
    var date = new Date(String(c.created));
    var time = el("time", "afterword-date");
    if (!isNaN(date.getTime())) {
      time.dateTime = date.toISOString();
      var lang = document.documentElement.lang || undefined;
      try {
        time.textContent = new Intl.DateTimeFormat(lang, { dateStyle: "medium" }).format(date);
        time.title = new Intl.DateTimeFormat(lang, { dateStyle: "full", timeStyle: "short" }).format(date);
      } catch (e) {
        time.textContent = date.toDateString();
      }
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
    item.appendChild(this.body(c));
    return item;
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

  Widget.prototype.field = function (kind, label, control) {
    var id = "afterword-" + kind + "-" + this.n;
    var row = el("p", "afterword-field afterword-field--" + kind);
    var lab = el("label", "afterword-label", label);
    lab.htmlFor = id;
    control.id = id;
    row.appendChild(lab);
    row.appendChild(control);
    return row;
  };

  Widget.prototype.form = function (cfg) {
    var self = this;
    var form = el("form", "afterword-form");
    form.setAttribute("novalidate", "");
    if (this.t("formHeading")) {
      form.appendChild(el("h" + Math.min(6, this.level + 1), "afterword-form-heading", this.t("formHeading")));
    }

    var name = el("input", "afterword-input");
    name.name = "author";
    name.type = "text";
    name.required = true;
    name.maxLength = cfg.maxName || 80;
    name.autocomplete = "name";
    form.appendChild(this.field("name", this.t("name"), name));

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
    message.maxLength = cfg.maxBody || 5000;
    var hintId = "afterword-hint-" + this.n;
    message.setAttribute("aria-describedby", hintId);
    form.appendChild(this.field("message", this.t("message"), message));

    var hintKey = cfg.formatting === "basic" ? (cfg.links ? "hintBasic" : "hintBasicNoLinks") : "hintPlain";
    var hint = el("p", "afterword-hint", this.t(hintKey));
    hint.id = hintId;
    form.appendChild(hint);

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
        if (typeof saved.name === "string") name.value = saved.name;
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
      if (!name.value.trim()) return invalid(name, "errorNameRequired");
      if (email && email.value.trim() && !email.checkValidity()) return invalid(email, "errorEmailInvalid");
      if (!message.value.trim()) return invalid(message, "errorBodyRequired");

      self.busy = true;
      button.disabled = true;
      button.textContent = self.t("sending");
      form.setAttribute("aria-busy", "true");
      var payload = {
        thread: self.thread,
        page: location.origin + location.pathname,
        author: name.value,
        email: email ? email.value : "",
        body: message.value,
        token: self.token,
        website: trapInput.value
      };
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
          if (self.remember) {
            try {
              localStorage.setItem("afterword:identity",
                JSON.stringify({ name: name.value, email: email ? email.value : "" }));
            } catch (e) { /* storage unavailable */ }
          }
          if (data.status === "published" && data.comment) {
            var item = self.comment(data.comment);
            if (item) {
              var newestFirst = self.list.firstChild && self.order === "newest";
              self.list.insertBefore(item, newestFirst ? self.list.firstChild : null);
            }
            self.syncEmpty();
            show("success", self.t("published"));
          } else {
            show("pending", self.t("pending"));
          }
          self.emit("posted", { thread: self.thread, status: data.status || "pending" });
        } else {
          var key = data.error ? "error" + camel("_" + data.error) : "";
          var text = (key && self.strings[key]) || data.message || self.t("errorGeneric");
          show("error", text);
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

  window.Afterword = { init: init, version: "1.0.0" };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(); });
  } else {
    init();
  }
})();
