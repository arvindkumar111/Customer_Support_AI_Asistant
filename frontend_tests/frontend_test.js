// Runs the REAL index.html + app.js inside jsdom with a scripted fetch, and checks what the user would see.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const html = fs.readFileSync(require("path").join(__dirname, "..", "app", "static", "index.html"), "utf8");
const js = fs.readFileSync(require("path").join(__dirname, "..", "app", "static", "app.js"), "utf8");
let passed = 0, failed = 0;
function check(name, cond) { console.log((cond ? "PASS  " : "FAIL  ") + name); if (!cond) { process.exitCode = 1; failed++; } else { passed++; } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function boot(routes) {
  const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true, url: "http://localhost:8000/" });
  const calls = [];
  dom.window.fetch = (path, opts) => {
    opts = opts || {};
    calls.push({ path, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null, credentials: opts.credentials });
    const handler = routes[(opts.method || "GET") + " " + path];
    if (!handler) { return Promise.reject(new Error("network down")); }
    const r = typeof handler === "function" ? handler(opts) : handler;
    if (r && typeof r.then === "function") { return r; }   // a handler may return a pending promise to simulate a slow server
    return Promise.resolve({ ok: r.status < 400, status: r.status, json: () => (r.json === undefined ? Promise.reject(new Error("no json")) : Promise.resolve(r.json)) });
  };
  dom.window.eval(js);
  await wait(30);
  return { dom, d: dom.window.document, calls, w: dom.window };
}
const texts = (d) => Array.from(d.querySelectorAll("#log .msg")).map((e) => e.textContent);
const shown = (d, id) => !d.getElementById(id).hidden;
const typeAndSend = async (w, d, text) => { const m = d.getElementById("msg"); m.value = text; d.getElementById("form").dispatchEvent(new w.Event("submit", { cancelable: true })); await wait(30); };

(async () => {
  // ---- not logged in: login panel with the customer list ----
  let t = await boot({
    "GET /api/me": { status: 401, json: {} },
    "GET /api/demo-customers": { status: 200, json: [{ customer_id: "C001", name: "Aarav Sharma" }, { customer_id: "C002", name: "Priya Verma" }] },
  });
  check("not logged in -> the login panel is shown, the chat is hidden", shown(t.d, "login") && !shown(t.d, "chat"));
  check("the demo customers fill the dropdown", t.d.querySelectorAll("#customer option").length === 2 && t.d.querySelector("#customer option").textContent === "Aarav Sharma (C001)");
  check("every request sends the cookie (same-origin credentials)", t.calls.every((c) => c.credentials === "same-origin"));

  // ---- guest chat ----
  t.d.getElementById("guest-btn").click();
  check("guest -> chat opens with a greeting that mentions logging in", shown(t.d, "chat") && /Log in/.test(texts(t.d)[0]));
  check("guest -> no 'signed in' label", t.d.getElementById("who").hidden);

  // ---- login ----
  t = await boot({
    "GET /api/me": { status: 401, json: {} },
    "GET /api/demo-customers": { status: 200, json: [{ customer_id: "C001", name: "Aarav Sharma" }] },
    "POST /api/login": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } },
  });
  t.d.getElementById("login-btn").click(); await wait(30);
  const loginCall = t.calls.find((c) => c.path === "/api/login");
  check("login sends only the chosen customer id", JSON.stringify(loginCall.body) === '{"customer_id":"C001"}');
  check("after login: chat shown, name shown, personal greeting", shown(t.d, "chat") && t.d.getElementById("who-text").textContent === "Signed in as Aarav Sharma" && /Hello Aarav/.test(texts(t.d)[0]));

  // ---- already logged in on page load ----
  t = await boot({ "GET /api/me": { status: 200, json: { customer_id: "C002", name: "Priya Verma" } } });
  check("existing session -> straight to the chat", shown(t.d, "chat") && !shown(t.d, "login") && /Hello Priya/.test(texts(t.d)[0]));

  // ---- chat: reply, handoff ticket, XSS ----
  t = await boot({
    "GET /api/me": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } },
    "POST /api/chat": (o) => {
      const m = JSON.parse(o.body).message;
      if (m.includes("manager")) return { status: 200, json: { reply: "I'm connecting you to a human agent.", handoff: true, ticket_id: 42, request_id: "abc" } };
      if (m.includes("xss")) return { status: 200, json: { reply: '<img src=x onerror="window.pwned=1"><script>window.pwned=1<\/script>**bold**', handoff: false, ticket_id: null } };
      if (m.includes("multi")) return { status: 200, json: { reply: "line one\n\nline two", handoff: false, ticket_id: null } };
      if (m.includes("limit")) return { status: 413, json: { error: "request_too_large" } };
      if (m.includes("boom")) return { status: 500, json: { error: "internal_error", reply: "Something went wrong on our side.", handoff: false } };
      if (m.includes("badjson")) return { status: 502 };
      return { status: 200, json: { reply: "Aap 7 days mein return kar sakte hain.", handoff: false, ticket_id: null, request_id: "r1" } };
    },
  });
  await typeAndSend(t.w, t.d, "Return policy?");
  let log = texts(t.d);
  check("the user's message and the reply both appear", log.includes("Return policy?") && log.includes("Aap 7 days mein return kar sakte hain."));
  const chatCall = t.calls.filter((c) => c.path === "/api/chat").pop();
  check("the chat request carries ONLY the message (no customer id from the browser)", JSON.stringify(chatCall.body) === '{"message":"Return policy?"}');
  check("input is cleared and enabled again after the reply", t.d.getElementById("msg").value === "" && !t.d.getElementById("msg").disabled && !t.d.getElementById("send").disabled);
  check("the typing indicator is gone after the reply", t.d.querySelectorAll(".typing").length === 0);

  await typeAndSend(t.w, t.d, "I want the manager");
  check("a handoff shows the ticket number", /Ticket #42/.test(t.d.querySelector(".ticket").textContent));

  await typeAndSend(t.w, t.d, "xss test");
  check("HTML in a reply is shown as plain text (no img element, no script element)", t.d.querySelectorAll("#log img, #log script").length === 0 && t.d.querySelector("#log").textContent.includes("<img src=x"));
  check("...and nothing executed", t.w.pwned === undefined);
  await typeAndSend(t.w, t.d, "multi line");
  check("line breaks in a reply are kept (pre-wrap, textContent)", texts(t.d).some((x) => x === "line one\n\nline two"));
  await typeAndSend(t.w, t.d, "limit please");
  check("413 -> a clear 'too large' message", texts(t.d).some((x) => /too large/.test(x)));
  await typeAndSend(t.w, t.d, "boom");
  check("500 with a reply -> that generic reply is shown, as an error", texts(t.d).some((x) => x === "Something went wrong on our side.") && t.d.querySelector(".msg.err") !== null);
  await typeAndSend(t.w, t.d, "badjson");
  check("a non-JSON error response -> a generic retry message, no crash", texts(t.d).some((x) => /Something went wrong\. Please try again/.test(x)));
  check("the chat is usable again after errors", !t.d.getElementById("send").disabled);

  // ---- network failure ----
  t = await boot({ "GET /api/me": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } } });
  await typeAndSend(t.w, t.d, "hello");
  check("network failure -> a clear message and the input is enabled again", texts(t.d).some((x) => /could not reach the server/.test(x)) && !t.d.getElementById("msg").disabled);

  // ---- keyboard, chips, empty, double send ----
  let chatCalls = 0;
  t = await boot({
    "GET /api/me": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } },
    "POST /api/chat": () => { chatCalls++; return { status: 200, json: { reply: "ok", handoff: false } }; },
  });
  const msg = t.d.getElementById("msg");
  msg.value = "via enter"; msg.dispatchEvent(new t.w.KeyboardEvent("keydown", { key: "Enter", cancelable: true })); await wait(30);
  check("Enter sends", chatCalls === 1);
  msg.value = "line"; msg.dispatchEvent(new t.w.KeyboardEvent("keydown", { key: "Enter", shiftKey: true, cancelable: true })); await wait(30);
  check("Shift+Enter does NOT send", chatCalls === 1);
  msg.value = "   "; t.d.getElementById("form").dispatchEvent(new t.w.Event("submit", { cancelable: true })); await wait(30);
  check("a blank message is not sent", chatCalls === 1);
  t.d.querySelector(".chip").click(); await wait(30);
  check("a suggestion chip sends its text", chatCalls === 2 && texts(t.d).includes("Where is my order?"));

  // ---- one request at a time ----
  let release; let inflight = 0; let maxInflight = 0;
  t = await boot({
    "GET /api/me": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } },
    "POST /api/chat": () => { inflight++; maxInflight = Math.max(maxInflight, inflight); return new Promise(() => {}); },
  });
  await typeAndSend(t.w, t.d, "first");
  check("while waiting: input and button are disabled and a typing indicator shows", t.d.getElementById("msg").disabled && t.d.getElementById("send").disabled && t.d.querySelectorAll(".typing").length === 1);
  t.d.getElementById("form").dispatchEvent(new t.w.Event("submit", { cancelable: true })); await wait(30);
  check("a second send while waiting is ignored", maxInflight === 1);

  // ---- logout ----
  t = await boot({
    "GET /api/me": { status: 200, json: { customer_id: "C001", name: "Aarav Sharma" } },
    "POST /api/logout": { status: 200, json: { ok: true } },
    "GET /api/demo-customers": { status: 200, json: [{ customer_id: "C001", name: "Aarav Sharma" }] },
  });
  t.d.getElementById("logout").click(); await wait(40);
  check("logout -> back to the login panel with the conversation cleared", shown(t.d, "login") && !shown(t.d, "chat") && t.d.querySelectorAll("#log .msg").length === 0);

  // ---- demo login disabled (production) ----
  t = await boot({ "GET /api/me": { status: 401, json: {} }, "GET /api/demo-customers": { status: 404, json: {} } });
  check("demo login disabled -> the customer picker is hidden, guest chat remains", t.d.getElementById("login-form").hidden && shown(t.d, "login"));

  // ---- char counter ----
  t = await boot({ "GET /api/me": { status: 200, json: { customer_id: "C001", name: "A B" } } });
  const m2 = t.d.getElementById("msg"); m2.value = "x".repeat(850); m2.dispatchEvent(new t.w.Event("input"));
  check("a counter appears near the 1000-character limit", !t.d.getElementById("count").hidden && t.d.getElementById("count").textContent === "850 / 1000");
  console.log(failed ? "\n" + failed + " FAILED, " + passed + " passed" : "\nall " + passed + " checks passed");
  process.exit(process.exitCode || 0);
})();
