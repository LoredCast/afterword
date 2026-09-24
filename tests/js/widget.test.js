// Widget tests. Run with:  node --test tests/js/
// Needs jsdom:  npm install --prefix tests/js jsdom   (or set JSDOM_PATH)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { JSDOM } = require(process.env.JSDOM_PATH || "jsdom");
const WIDGET = fs.readFileSync(path.join(__dirname, "../../afterword/static/widget.js"), "utf8");

function tick(ms = 0) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function setup({ html, url = "https://blog.example.com/posts/hello/?utm=x#top",
                 scriptSrc = "https://comments.example.com/widget.js", routes = {} } = {}) {
  const dom = new JSDOM(`<!doctype html><html lang="en"><head></head><body>${html}</body></html>`,
                        { url, runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  window.fetch = async (input, init = {}) => {
    const u = new URL(input);
    calls.push({ url: u, init, body: init.body ? JSON.parse(init.body) : null });
    const key = `${init.method || "GET"} ${u.pathname}`;
    const handler = routes[key];
    if (!handler) throw new TypeError("network error");
    const { status = 200, json } = handler(u, init, calls);
    return { ok: status >= 200 && status < 300, status, json: async () => json };
  };
  Object.defineProperty(window.document, "currentScript", { get: () => ({ src: scriptSrc }) });
  window.eval(WIDGET);
  return { window, document: window.document, calls };
}

const thread = (comments, extra = {}) => () => ({
  json: Object.assign({
    thread: "/posts/hello", open: true, order: "oldest", count: comments.length, comments,
    form: { token: "tok-1", maxName: 80, maxBody: 5000, email: true, formatting: "basic", links: true },
  }, extra),
});

test("hostile server data can never become markup", async () => {
  const { document } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: {
      "GET /api/v1/thread": thread([{
        id: "abc123", author: '<img src=x onerror="alert(1)">', created: "2026-09-24T10:00:00Z",
        text: "ignored",
        body: [[
          { t: "text", v: "<script>alert(2)</script>" },
          { t: "a", v: "javascript:alert(3)" },
          { t: "a", v: "https://user:pw@evil.example/" },
          { t: "a", v: "https://ok.example/page" },
          { t: "em", v: "<b>bold?</b>" },
          { t: "html", v: "<iframe src=//evil.example></iframe>" },
          { t: "code", v: "<svg onload=alert(4)>" },
        ]],
      }, { id: "../../etc", author: "Bad id", created: "nonsense", body: "not-an-array", text: "fallback <i>text</i>" }]),
    },
  });
  await tick(10);
  const root = document.querySelector("[data-afterword]");
  assert.equal(root.querySelectorAll("script, img, iframe, svg, b, i").length, 0);
  const links = [...root.querySelectorAll(".afterword-body a")];
  assert.deepEqual(links.map((a) => a.href), ["https://ok.example/page"]);
  assert.equal(links[0].rel, "nofollow ugc noopener noreferrer");
  assert.match(root.querySelector(".afterword-author").textContent, /<img src=x/);
  assert.match(root.textContent, /javascript:alert\(3\)/);          // shown as text
  const items = root.querySelectorAll(".afterword-comment");
  assert.equal(items[0].id, "comment-abc123");
  assert.equal(items[1].id, "");                                     // invalid id dropped
  assert.match(items[1].querySelector(".afterword-body").textContent, /fallback <i>text<\/i>/);
});

test("documented structure and classes", async () => {
  const { document } = setup({
    html: '<section data-afterword data-thread="t1"></section>',
    routes: {
      "GET /api/v1/thread": thread([
        { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z",
          body: [[{ t: "text", v: "Hello" }, { t: "br" }, { t: "text", v: "there" }], [{ t: "text", v: "Two" }]] },
        { id: "b2", author: "Grace", created: "2026-09-21T08:00:00Z", body: [[{ t: "text", v: "Hi" }]] },
      ]),
    },
  });
  await tick(10);
  const root = document.querySelector("section");
  assert.ok(root.classList.contains("afterword"));
  assert.ok(root.classList.contains("afterword--ready"));
  assert.equal(root.querySelector("h2.afterword-heading .afterword-count").textContent, "2");
  assert.equal(root.querySelectorAll("ol.afterword-list > li.afterword-comment").length, 2);
  const first = root.querySelector(".afterword-comment");
  assert.equal(first.querySelector(".afterword-meta .afterword-author").textContent, "Ada");
  assert.equal(first.querySelector("a.afterword-permalink").getAttribute("href"), "#comment-a1");
  assert.equal(first.querySelector("time.afterword-date").dateTime, "2026-09-20T08:00:00.000Z");
  assert.equal(first.querySelectorAll(".afterword-body > p").length, 2);
  assert.equal(first.querySelectorAll(".afterword-body br").length, 1);
  assert.ok(root.querySelector(".afterword-empty").hidden);

  const form = root.querySelector("form.afterword-form");
  assert.ok(form.querySelector("h3.afterword-form-heading"));
  for (const [kind, selector] of [["name", "input.afterword-input"], ["email", "input.afterword-input"],
                                  ["message", "textarea.afterword-textarea"]]) {
    const field = form.querySelector(`.afterword-field.afterword-field--${kind}`);
    const control = field.querySelector(selector);
    assert.equal(field.querySelector("label.afterword-label").htmlFor, control.id);
  }
  assert.equal(form.querySelector("textarea").maxLength, 5000);
  assert.match(form.querySelector(".afterword-hint").textContent, /Links/);
  const trap = form.querySelector(".afterword-hp");
  assert.equal(trap.getAttribute("aria-hidden"), "true");
  assert.equal(trap.querySelector("input").name, "website");
  assert.equal(trap.querySelector("input").tabIndex, -1);
  assert.equal(form.querySelector("button.afterword-submit").type, "submit");
  assert.ok(form.querySelector(".afterword-notice").hidden);
});

test("thread ids: explicit, path and canonical", async () => {
  const seen = [];
  const routes = { "GET /api/v1/thread": (u) => { seen.push(u.searchParams.get("id")); return thread([])(); } };
  setup({ html: '<div data-afterword data-thread=" wf-abc123 "></div>', routes });
  setup({ html: "<div data-afterword></div>", routes });
  // canonical requested but no canonical link: falls back to the path
  setup({ html: '<div data-afterword data-thread-from="canonical"></div>', routes,
          url: "https://blog.example.com/?p=42" });
  await tick(10);
  assert.deepEqual(seen, ["wf-abc123", "/posts/hello", "/"]);
  // canonical link present before the widget runs
  const seen2 = [];
  const dom = new JSDOM('<!doctype html><head><link rel="canonical" href="https://blog.example.com/my-post/"></head>' +
                        '<body><div data-afterword data-thread-from="canonical"></div></body>',
                        { url: "https://blog.example.com/?p=42", runScripts: "outside-only" });
  dom.window.fetch = async (input) => { seen2.push(new URL(input).searchParams.get("id")); return { ok: true, status: 200, json: async () => thread([])().json }; };
  Object.defineProperty(dom.window.document, "currentScript", { get: () => ({ src: "https://c.example/widget.js" }) });
  dom.window.eval(WIDGET);
  await tick(10);
  assert.deepEqual(seen2, ["/my-post"]);
});

test("server address comes from the script's own URL, including sub-paths", async () => {
  const { calls } = setup({ html: "<div data-afterword></div>", scriptSrc: "https://example.org/comments/widget.js",
                            routes: { "GET /comments/api/v1/thread": thread([]) } });
  await tick(10);
  assert.equal(calls[0].url.href, "https://example.org/comments/api/v1/thread?id=%2Fposts%2Fhello");
  assert.equal(calls[0].init.credentials, "omit");
});

test("posting: payload, published comment appears, token rotates", async () => {
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: {
      "GET /api/v1/thread": thread([]),
      "POST /api/v1/comments": () => ({ status: 201, json: {
        status: "published", token: "tok-2",
        comment: { id: "n3w", author: "Ada", created: "2026-09-24T10:00:00Z", body: [[{ t: "text", v: "Hi!" }]] } } }),
    },
  });
  await tick(10);
  const form = document.querySelector("form");
  form.querySelector("[name=author]").value = "Ada";
  form.querySelector("[name=email]").value = "ada@example.com";
  form.querySelector("[name=body]").value = "Hi!";
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await tick(10);
  const post = calls.find((c) => c.init.method === "POST");
  assert.equal(post.init.headers["Content-Type"], "application/json");
  assert.equal(post.init.credentials, "omit");
  assert.deepEqual(post.body, { thread: "t1", page: "https://blog.example.com/posts/hello/", author: "Ada",
                                email: "ada@example.com", body: "Hi!", token: "tok-1", website: "" });
  assert.equal(document.querySelectorAll(".afterword-comment").length, 1);
  assert.equal(document.querySelector(".afterword-count").textContent, "1");
  assert.ok(document.querySelector(".afterword-empty").hidden);
  const notice = document.querySelector(".afterword-notice");
  assert.ok(notice.classList.contains("afterword-notice--success"));
  assert.equal(form.querySelector("[name=body]").value, "");
  assert.equal(form.querySelector("[name=author]").value, "Ada");
  form.querySelector("[name=body]").value = "Again";
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await tick(10);
  assert.equal(calls.filter((c) => c.init.method === "POST")[1].body.token, "tok-2");
});

test("pending and error messages, with overridable text", async () => {
  let reply = { status: 202, json: { status: "pending", token: "t" } };
  const { window, document } = setup({
    html: '<div data-afterword data-thread="t1" data-text-error-too-fast="Bitte kurz warten."></div>',
    routes: { "GET /api/v1/thread": thread([]), "POST /api/v1/comments": () => reply },
  });
  await tick(10);
  const form = document.querySelector("form");
  const send = async () => {
    form.querySelector("[name=author]").value = "Ada";
    form.querySelector("[name=body]").value = "Hello";
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick(10);
    return document.querySelector(".afterword-notice");
  };
  let notice = await send();
  assert.ok(notice.classList.contains("afterword-notice--pending"));
  assert.equal(document.querySelectorAll(".afterword-comment").length, 0);

  reply = { status: 400, json: { error: "too_fast", message: "server text" } };
  notice = await send();
  assert.equal(notice.textContent, "Bitte kurz warten.");
  assert.ok(notice.classList.contains("afterword-notice--error"));

  reply = { status: 400, json: { error: "something_new", message: "Server says <b>no</b>" } };
  notice = await send();
  assert.equal(notice.textContent, "Server says <b>no</b>");
  assert.equal(notice.querySelectorAll("b").length, 0);

  reply = { status: 400, json: { error: "body_too_long", message: "Too long", field: "body" } };
  await send();
  assert.equal(form.querySelector("[name=body]").getAttribute("aria-invalid"), "true");
});

test("client-side checks stop empty submissions", async () => {
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": thread([]), "POST /api/v1/comments": () => ({ status: 201, json: {} }) },
  });
  await tick(10);
  const form = document.querySelector("form");
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await tick(5);
  assert.equal(calls.filter((c) => c.init.method === "POST").length, 0);
  assert.equal(form.querySelector("[name=author]").getAttribute("aria-invalid"), "true");
});

test("load failure and closed threads", async () => {
  const failed = setup({ html: '<div data-afterword data-thread="t1"></div>', routes: {} });
  await tick(10);
  const root = failed.document.querySelector("[data-afterword]");
  assert.ok(root.classList.contains("afterword--error"));
  assert.equal(root.querySelector(".afterword-status").textContent, "Comments could not be loaded.");

  const closed = setup({ html: '<div data-afterword data-thread="t1"></div>',
                         routes: { "GET /api/v1/thread": thread([], { open: false, form: null }) } });
  await tick(10);
  const croot = closed.document.querySelector("[data-afterword]");
  assert.ok(croot.classList.contains("afterword--closed"));
  assert.equal(croot.querySelector("form"), null);
  assert.ok(croot.querySelector(".afterword-closed"));
});

test("email field and hints follow server settings; loaded event fires", async () => {
  const { window, document } = setup({
    html: '<div data-afterword data-thread="t1" data-heading-level="3" data-text-heading=""></div>',
    routes: { "GET /api/v1/thread": thread([], { form: { token: "x", email: false, formatting: "plain", links: false } }) },
  });
  let detail = null;
  document.querySelector("[data-afterword]").addEventListener("afterword:loaded", (e) => { detail = e.detail; });
  await tick(10);
  assert.equal(document.querySelector("[name=email]"), null);
  assert.equal(document.querySelector(".afterword-heading"), null);      // heading text set to empty
  assert.ok(document.querySelector("h4.afterword-form-heading"));
  assert.match(document.querySelector(".afterword-hint").textContent, /Line breaks are kept/);
  assert.deepEqual({ ...detail }, { thread: "t1", count: 0 });
  assert.ok(window.Afterword && typeof window.Afterword.init === "function");
});
