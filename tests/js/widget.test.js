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
                 scriptSrc = "https://comments.example.com/widget.js", routes = {}, storage = {} } = {}) {
  const dom = new JSDOM(`<!doctype html><html lang="en"><head></head><body>${html}</body></html>`,
                        { url, runScripts: "outside-only" });
  const { window } = dom;
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, JSON.stringify(value));
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

// -- pseudonyms -------------------------------------------------------------------

const KEY = "abcdefghijklmnopqrstuvwxyz234567";
const DASHED = "abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567";
const withPseudonyms = (comments = [], extra = {}) => thread(comments, Object.assign({ pseudonyms: true }, extra));
const stored = (window, slot = "afterword:pseudonym") => JSON.parse(window.localStorage.getItem(slot) || "null");

async function submit(window, form, fields = {}) {
  for (const [name, value] of Object.entries(fields)) form.querySelector(`[name=${name}]`).value = value;
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await tick(10);
}

test("verified and unverified marks come only from the server's verified flag", async () => {
  const comments = [
    { id: "a1", author: "Mara", created: "2026-09-20T08:00:00Z", verified: true, body: [[{ t: "text", v: "Held" }]] },
    { id: "b2", author: "Mara verified", created: "2026-09-21T08:00:00Z", verified: false, body: [] },
    { id: "c3", author: "Old", created: "2026-09-22T08:00:00Z", verified: "yes", body: [] },
    { id: "d4", author: "Older", created: "2026-09-22T08:00:00Z", body: [] },
  ];
  const on = setup({ html: '<div data-afterword data-thread="t1"></div>',
                     routes: { "GET /api/v1/thread": withPseudonyms(comments) } });
  await tick(10);
  const items = [...on.document.querySelectorAll(".afterword-comment")];
  assert.equal(items[0].querySelector(".afterword-meta .afterword-verified").textContent, "verified");
  assert.ok(items[0].classList.contains("afterword-comment--verified"));
  assert.match(items[0].querySelector(".afterword-verified").title, /holds the key/);
  for (const item of items.slice(1)) {
    assert.equal(item.querySelector(".afterword-verified"), null);
    assert.equal(item.querySelector(".afterword-unverified").textContent, "unverified");
    assert.ok(!item.classList.contains("afterword-comment--verified"));
  }

  const off = setup({ html: '<div data-afterword data-thread="t1" data-text-unverified=""></div>',
                      routes: { "GET /api/v1/thread": thread(comments) } });
  await tick(10);
  assert.equal(off.document.querySelectorAll(".afterword-verified").length, 1);   // still true for that comment
  assert.equal(off.document.querySelectorAll(".afterword-unverified").length, 0);
  assert.equal(off.document.querySelector(".afterword-pseudonym"), null);
});

test("keeping a name: key made in the browser, sent with the comment, backup offered", async () => {
  let replies = 0;
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: {
      "GET /api/v1/thread": withPseudonyms(),
      "POST /api/v1/comments": (u, init) => ({ status: 202, json: {
        status: "pending", token: "tok-" + (++replies + 1), pseudonym: { name: JSON.parse(init.body).author.trim() } } }),
    },
  });
  await tick(10);
  const form = document.querySelector("form");
  const box = form.querySelector(".afterword-pseudonym");
  assert.ok(box.compareDocumentPosition(form.querySelector(".afterword-field--name")) & window.Node.DOCUMENT_POSITION_PRECEDING);
  assert.match(box.querySelector(".afterword-pseudonym-policy").textContent, /unverified/);
  const keep = box.querySelector("input[type=checkbox][name=keep]");
  assert.equal(box.querySelector(`label[for="${keep.id}"]`).textContent, "Keep this name as my pseudonym");
  const help = box.querySelector(".afterword-pseudonym-help");
  assert.ok(help.hidden);
  keep.checked = true;
  keep.dispatchEvent(new window.Event("change"));
  assert.ok(!help.hidden);
  assert.match(help.textContent, /No account, no email/);
  assert.match(help.textContent, /publicly linked/);
  assert.match(help.textContent, /network address/);
  assert.match(help.textContent, /backup key/);

  await submit(window, form, { author: " Mara ", body: "First!" });
  const first = calls.filter((c) => c.init.method === "POST")[0].body;
  assert.match(first.key, /^[a-z2-7]{32}$/);
  assert.equal(first.author, " Mara ");
  const held = stored(window);
  assert.deepEqual(held, { name: "Mara", key: first.key });
  assert.equal(stored(window, "afterword:pseudonym-pending"), null);

  const nameRow = form.querySelector(".afterword-field--name");
  assert.ok(nameRow.hidden);
  const now = form.querySelector(".afterword-pseudonym--held");
  assert.equal(now.querySelector(".afterword-posting-as").textContent, "Posting as Mara verified");
  assert.match(now.querySelector(".afterword-notice--success").textContent, /Save your backup key/);
  const backup = now.querySelector("details.afterword-backup");
  assert.ok(backup.open);
  const dashed = first.key.match(/.{4}/g).join("-");
  assert.equal(backup.querySelector("input.afterword-backup-key").value, dashed);
  assert.ok(backup.querySelector("input.afterword-backup-key").readOnly);
  const download = backup.querySelector("a.afterword-download");
  assert.equal(download.getAttribute("download"), "afterword-pseudonym.txt");
  assert.ok(decodeURIComponent(download.href).includes("Key:  " + dashed));
  assert.ok(decodeURIComponent(download.href).includes("Site: https://blog.example.com"));

  await submit(window, form, { body: "Second" });
  const second = calls.filter((c) => c.init.method === "POST")[1].body;
  assert.equal(second.key, first.key);
  assert.equal(second.author, "Mara");
  // Loading comments never carries the key.
  for (const call of calls.filter((c) => c.init.method !== "POST")) {
    assert.equal(call.body, null);
    assert.ok(!call.url.href.includes(first.key));
  }
});

test("a refused claim stores nothing; a retry for the same name reuses the key", async () => {
  let reply = { status: 409, json: { error: "pseudonym_taken", message: "server", field: "author", token: "t2" } };
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": withPseudonyms(), "POST /api/v1/comments": () => reply },
  });
  await tick(10);
  const form = document.querySelector("form");
  form.querySelector("[name=keep]").checked = true;
  await submit(window, form, { author: "Mara", body: "Hi" });
  assert.match(form.querySelector(".afterword-notice--error").textContent, /already someone’s pseudonym/);
  assert.equal(form.querySelector("[name=author]").getAttribute("aria-invalid"), "true");
  assert.equal(stored(window), null);
  reply = { status: 202, json: { status: "pending", token: "t3", pseudonym: { name: "Mara" } } };
  await submit(window, form, { author: "Mara", body: "Hi" });
  const posts = calls.filter((c) => c.init.method === "POST").map((c) => c.body.key);
  assert.equal(posts[0], posts[1]);
  assert.equal(stored(window).key, posts[0]);

  // Unticked: no key at all, and the server's naming-policy message is shown.
  const plain = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": withPseudonyms(),
              "POST /api/v1/comments": () => ({ status: 409, json: { error: "name_reserved", field: "author" } }) },
  });
  await tick(10);
  const plainForm = plain.document.querySelector("form");
  await submit(plain.window, plainForm, { author: "Mara", body: "Hi" });
  assert.equal("key" in plain.calls.find((c) => c.init.method === "POST").body, false);
  assert.match(plainForm.querySelector(".afterword-notice").textContent, /restore your backup key/);
});

test("restoring from a pasted backup file", async () => {
  let answer = { status: 404, json: { error: "pseudonym_unknown", message: "No." } };
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": withPseudonyms(), "POST /api/v1/pseudonym": () => answer,
              "POST /api/v1/comments": () => ({ status: 201, json: {} }) },
  });
  await tick(10);
  const form = document.querySelector("form");
  const restore = form.querySelector("details.afterword-restore");
  assert.equal(restore.querySelector("summary").textContent, "Restore a pseudonym from a backup key");
  const input = restore.querySelector("input[name=restore]");
  const button = restore.querySelector("button.afterword-restore-button");
  assert.equal(button.type, "button");

  input.value = "not a key";
  button.click();
  await tick(10);
  assert.equal(input.getAttribute("aria-invalid"), "true");
  assert.equal(calls.filter((c) => c.url.pathname === "/api/v1/pseudonym").length, 0);

  input.value = DASHED;
  button.click();
  await tick(10);
  assert.equal(restore.querySelector(".afterword-notice--error").textContent, "No pseudonym on this site uses that key.");
  assert.equal(stored(window), null);

  answer = { status: 200, json: { pseudonym: { name: "Mara" } } };
  input.value = `Afterword pseudonym backup\n\nName: Mara\nSite: https://blog.example.com\nKey:  ${DASHED.toUpperCase()}\n`;
  input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", cancelable: true }));
  await tick(10);
  const lookups = calls.filter((c) => c.url.pathname === "/api/v1/pseudonym");
  assert.deepEqual(lookups[lookups.length - 1].body, { key: KEY });
  assert.equal(lookups[lookups.length - 1].init.credentials, "omit");
  assert.equal(calls.filter((c) => c.url.pathname === "/api/v1/comments").length, 0);   // Enter did not post
  assert.deepEqual(stored(window), { name: "Mara", key: KEY });
  assert.match(form.querySelector(".afterword-posting-as").textContent, /Posting as Mara/);
  assert.match(form.querySelector(".afterword-pseudonym .afterword-notice--success").textContent, /Welcome back/);
});

test("stop using a pseudonym on this device, after confirming", async () => {
  const { window, document } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    storage: { "afterword:pseudonym": { name: "Mara", key: KEY } },
    routes: { "GET /api/v1/thread": withPseudonyms() },
  });
  await tick(10);
  const questions = [];
  let answer = false;
  window.confirm = (text) => { questions.push(text); return answer; };
  const form = document.querySelector("form");
  assert.ok(form.querySelector(".afterword-field--name").hidden);
  assert.ok(!form.querySelector("details.afterword-backup").open);
  form.querySelector("button.afterword-forget").click();
  assert.match(questions[0], /Without your backup key you cannot post as Mara again/);
  assert.deepEqual(stored(window), { name: "Mara", key: KEY });
  answer = true;
  form.querySelector("button.afterword-forget").click();
  assert.equal(stored(window), null);
  assert.ok(!form.querySelector(".afterword-field--name").hidden);
  assert.equal(form.querySelector("[name=author]").value, "");
  assert.ok(form.querySelector("[name=keep]"));
});

test("a held name that was released and taken by someone else is explained", async () => {
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1" data-remember="true"></div>',
    storage: { "afterword:pseudonym": { name: "Mara", key: KEY }, "afterword:identity": { name: "Ada" } },
    routes: { "GET /api/v1/thread": withPseudonyms(),
              "POST /api/v1/comments": () => ({ status: 409, json: { error: "pseudonym_taken", field: "author" } }) },
  });
  await tick(10);
  const form = document.querySelector("form");
  await submit(window, form, { body: "Hello again" });
  const post = calls.find((c) => c.init.method === "POST").body;
  assert.equal(post.author, "Mara");                  // the held name wins over a remembered one
  assert.equal(post.key, KEY);
  assert.match(form.querySelector(".afterword-notice--error").textContent, /^Mara is no longer held by your key/);
});

test("with pseudonyms off, a stored key is never sent", async () => {
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1" data-remember="true"></div>',
    storage: { "afterword:pseudonym": { name: "Mara", key: KEY }, "afterword:identity": { name: "Ada" } },
    routes: { "GET /api/v1/thread": thread([]), "POST /api/v1/comments": () => ({ status: 202, json: { status: "pending" } }) },
  });
  await tick(10);
  const form = document.querySelector("form");
  assert.equal(form.querySelector(".afterword-pseudonym"), null);
  assert.equal(form.querySelector("[name=author]").value, "Ada");
  await submit(window, form, { body: "Hi" });
  const post = calls.find((c) => c.init.method === "POST").body;
  assert.equal(post.author, "Ada");
  assert.equal("key" in post, false);
  assert.deepEqual(stored(window), { name: "Mara", key: KEY });   // kept for when they come back on
});

// -- collapsed form and replies ---------------------------------------------------------

const withReplies = (comments, extra = {}) => () => {
  const reply = thread(comments, extra)();
  reply.json.form.replies = true;
  return reply;
};

test("the form is collapsed behind a button until the reader wants to write", async () => {
  const { window, document } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": thread([{ id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z", body: [] }]) },
  });
  await tick(10);
  const root = document.querySelector("[data-afterword]");
  const form = root.querySelector("form.afterword-form");
  const button = root.querySelector(".afterword-compose > button.afterword-compose-button");
  assert.ok(form.hidden);
  assert.ok(!root.querySelector(".afterword-list").hidden);         // comments are open
  assert.equal(button.textContent, "Write a comment");
  assert.equal(button.type, "button");
  assert.equal(button.getAttribute("aria-controls"), form.id);
  assert.equal(button.getAttribute("aria-expanded"), "false");
  // The compose button comes after the comments, where the form will open.
  assert.ok(root.querySelector(".afterword-list").compareDocumentPosition(button) & window.Node.DOCUMENT_POSITION_FOLLOWING);
  button.click();
  assert.ok(!form.hidden);
  assert.ok(button.parentNode.hidden);
  assert.equal(button.getAttribute("aria-expanded"), "true");
  assert.equal(document.activeElement, form.querySelector("[name=author]"));

  const open = setup({ html: '<div data-afterword data-thread="t1" data-form="open"></div>',
                       routes: { "GET /api/v1/thread": thread([]) } });
  await tick(10);
  assert.ok(!open.document.querySelector("form").hidden);
  assert.equal(open.document.querySelector(".afterword-compose"), null);
});

test("replies are shown one level deep with an @name and date reference", async () => {
  const { document } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": withReplies([
      { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z", body: [[{ t: "text", v: "Top" }]], replies: [
        { id: "r1", author: "Grace", created: "2026-09-20T09:00:00Z", parent: "a1",
          reply_to: { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z" }, body: [[{ t: "text", v: "Hi Ada" }]],
          replies: [{ id: "deep", author: "Too deep", created: "2026-09-20T10:00:00Z", body: [] }] },
        { id: "r2", author: "Eve", created: "2026-09-20T11:00:00Z", parent: "a1",
          reply_to: { id: null, author: "<img src=x onerror=alert(1)>", created: "2026-09-20T09:30:00Z" }, body: [] },
        { id: "r3", author: "Bad", created: "2026-09-20T12:00:00Z",
          reply_to: { id: "../../x", author: "Grace", created: "nonsense" }, body: [] },
      ] },
    ]) },
  });
  await tick(10);
  const root = document.querySelector("[data-afterword]");
  const top = root.querySelector(".afterword-list > li#comment-a1");
  const replies = [...top.querySelectorAll(":scope > ol.afterword-replies > li.afterword-comment--reply")];
  assert.deepEqual(replies.map((r) => r.id), ["comment-r1", "comment-r2", "comment-r3"]);
  assert.equal(root.querySelector("#comment-deep"), null);           // never more than one level
  assert.equal(root.querySelector(".afterword-count").textContent, "4");
  const link = replies[0].querySelector(".afterword-reply-to a.afterword-reply-ref");
  assert.equal(link.getAttribute("href"), "#comment-a1");
  assert.match(link.textContent, /^@Ada · Sep 20, 2026, \d{1,2}:\d{2}/);
  // A reference to a comment that is not on the page is plain text; hostile names stay text.
  assert.equal(replies[1].querySelector(".afterword-reply-to a"), null);
  assert.match(replies[1].querySelector(".afterword-reply-ref").textContent, /^@<img src=x onerror=alert\(1\)> · /);
  assert.equal(root.querySelectorAll("img").length, 0);
  assert.equal(replies[2].querySelector(".afterword-reply-to a"), null);
  assert.equal(replies[2].querySelector(".afterword-reply-ref").textContent, "@Grace");
  // Every comment can be answered, top-level ones and replies alike.
  assert.equal(root.querySelectorAll("button.afterword-reply-button").length, 4);
  assert.equal(top.querySelector(":scope > .afterword-comment-actions > button").textContent, "Reply");
});

test("replying: form opens with the reference, sends reply_to, and files the reply under its comment", async () => {
  let reply = null;
  const { window, document, calls } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: {
      "GET /api/v1/thread": withReplies([
        { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z", body: [], replies: [
          { id: "r1", author: "Grace", created: "2026-09-20T09:00:00Z", parent: "a1",
            reply_to: { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z" }, body: [] }] },
        { id: "b2", author: "Bob", created: "2026-09-21T08:00:00Z", body: [] },
      ]),
      "POST /api/v1/comments": () => reply,
    },
  });
  await tick(10);
  const root = document.querySelector("[data-afterword]");
  const form = root.querySelector("form");
  assert.ok(form.hidden);
  root.querySelector("#comment-r1 .afterword-reply-button").click();
  assert.ok(!form.hidden);
  const replying = form.querySelector(".afterword-replying");
  assert.ok(!replying.hidden);
  assert.match(replying.textContent, /^Replying to @Grace · Sep 20, 2026/);
  assert.equal(replying.querySelector("a").getAttribute("href"), "#comment-r1");
  assert.equal(document.activeElement, form.querySelector("[name=body]"));

  reply = { status: 201, json: { status: "published", token: "t2", comment: {
    id: "n3w", author: "Ada", created: "2026-09-22T08:00:00Z", parent: "a1",
    reply_to: { id: "r1", author: "Grace", created: "2026-09-20T09:00:00Z" }, body: [[{ t: "text", v: "Thanks" }]] } } };
  await submit(window, form, { author: "Ada", body: "Thanks" });
  const post = calls.find((c) => c.init.method === "POST").body;
  assert.equal(post.reply_to, "r1");
  const added = root.querySelector("#comment-a1 > ol.afterword-replies > li#comment-n3w");
  assert.ok(added);
  assert.ok(added.classList.contains("afterword-comment--reply"));
  assert.equal(root.querySelector(".afterword-count").textContent, "4");
  assert.ok(replying.hidden);                                      // reply mode ends after sending

  // A plain comment afterwards carries no reply_to; so does one after "Cancel reply".
  reply = { status: 202, json: { status: "pending", token: "t3" } };
  root.querySelector("#comment-b2 .afterword-reply-button").click();
  replying.querySelector("button.afterword-cancel-reply").click();
  assert.ok(replying.hidden);
  await submit(window, form, { author: "Ada", body: "Just a comment" });
  const posts = calls.filter((c) => c.init.method === "POST");
  assert.equal("reply_to" in posts[1].body, false);

  // A reply to a comment that is gone: the message is shown and reply mode ends.
  reply = { status: 400, json: { error: "reply_unavailable", message: "gone" } };
  root.querySelector("#comment-b2 .afterword-reply-button").click();
  await submit(window, form, { author: "Ada", body: "Too late" });
  assert.equal(form.querySelector(".afterword-notice").textContent, "The comment you are replying to is no longer available.");
  assert.ok(replying.hidden);
});

test("no reply buttons when replies are off, but existing replies are still shown", async () => {
  const { document } = setup({
    html: '<div data-afterword data-thread="t1"></div>',
    routes: { "GET /api/v1/thread": thread([
      { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z", body: [], replies: [
        { id: "r1", author: "Grace", created: "2026-09-20T09:00:00Z", parent: "a1",
          reply_to: { id: "a1", author: "Ada", created: "2026-09-20T08:00:00Z" }, body: [] }] }]) },
  });
  await tick(10);
  assert.equal(document.querySelectorAll(".afterword-reply-button").length, 0);
  assert.ok(document.querySelector("#comment-a1 .afterword-replies #comment-r1"));
});
