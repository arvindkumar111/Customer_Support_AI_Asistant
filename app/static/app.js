"use strict";
// Customer chat page. No framework. Replies are inserted with textContent only (never innerHTML), so a reply can
// never inject markup or script. The customer id is never sent from here: the server reads it from the session cookie.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var els = {
    login: $("login"), chat: $("chat"), who: $("who"), whoText: $("who-text"), logout: $("logout"),
    customer: $("customer"), loginBtn: $("login-btn"), guestBtn: $("guest-btn"), loginError: $("login-error"),
    log: $("log"), form: $("form"), msg: $("msg"), send: $("send"), chips: $("chips"), count: $("count")
  };
  var sending = false;

  function api(path, options) {
    options = options || {};
    options.credentials = "same-origin";
    if (options.body && !options.headers) { options.headers = { "Content-Type": "application/json" }; }
    return fetch(path, options);
  }

  function addMessage(text, who, extraClass) {
    var li = document.createElement("li");
    li.className = "msg " + who + (extraClass ? " " + extraClass : "");
    li.textContent = text;
    els.log.appendChild(li);
    els.log.scrollTop = els.log.scrollHeight;
    return li;
  }

  function addTypingIndicator() {
    var li = document.createElement("li");
    li.className = "msg bot";
    li.setAttribute("aria-label", "The assistant is typing");
    var dots = document.createElement("span");
    dots.className = "typing";
    for (var i = 0; i < 3; i++) { dots.appendChild(document.createElement("i")); }
    li.appendChild(dots);
    els.log.appendChild(li);
    els.log.scrollTop = els.log.scrollHeight;
    return li;
  }

  function setSending(value) {
    sending = value;
    els.send.disabled = value;
    els.msg.disabled = value;
    if (!value) { els.msg.focus(); }
  }

  function autosize() {
    els.msg.style.height = "auto";
    els.msg.style.height = Math.min(els.msg.scrollHeight, 140) + "px";
    var n = els.msg.value.length;
    els.count.hidden = n < 800;
    els.count.textContent = n + " / 1000";
  }

  function send(text) {
    text = (text || "").trim();
    if (!text || sending) { return; }
    addMessage(text, "me");
    els.msg.value = "";
    autosize();
    setSending(true);
    var typing = addTypingIndicator();
    var slow = setTimeout(function () {
      var note = document.createElement("span");
      note.textContent = " Still working on it...";
      typing.appendChild(note);
    }, 8000);

    api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (r) {
        typing.remove();
        if (r.data && r.data.reply) {
          var li = addMessage(r.data.reply, "bot", r.ok ? "" : "err");
          if (r.data.handoff) {
            var t = document.createElement("div");
            t.className = "ticket";
            t.textContent = r.data.ticket_id
              ? "A human agent will contact you. Ticket #" + r.data.ticket_id + "."
              : "A human agent will contact you.";
            li.appendChild(t);
          }
        } else if (r.status === 413) {
          addMessage("That message is too large. Please send a shorter one.", "bot", "err");
        } else {
          addMessage("Something went wrong. Please try again.", "bot", "err");
        }
      })
      .catch(function () {
        typing.remove();
        addMessage("I could not reach the server. Check your connection and try again.", "bot", "err");
      })
      .then(function () { clearTimeout(slow); setSending(false); });
  }

  function showChat(me) {
    els.login.hidden = true;
    els.chat.hidden = false;
    if (me) {
      els.who.hidden = false;
      els.whoText.textContent = "Signed in as " + me.name;
    } else {
      els.who.hidden = true;
    }
    if (!els.log.children.length) {
      addMessage(me
        ? "Hello " + me.name.split(" ")[0] + "! I can help with your orders, returns, refunds, warranty and shipping."
        : "Hello! I can answer questions about returns, refunds, warranty, shipping and cancellations. Log in to ask about your own orders.", "bot");
    }
    els.msg.focus();
  }

  function showLogin() {
    els.chat.hidden = true;
    els.login.hidden = false;
    api("/api/demo-customers").then(function (res) {
      if (!res.ok) { throw new Error("demo login disabled"); }
      return res.json();
    }).then(function (list) {
      els.customer.textContent = "";
      list.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.customer_id;
        o.textContent = c.name + " (" + c.customer_id + ")";
        els.customer.appendChild(o);
      });
    }).catch(function () {
      $("login-form").hidden = true;      // production: no demo login, only guest chat
    });
  }

  els.loginBtn.addEventListener("click", function () {
    els.loginError.hidden = true;
    api("/api/login", { method: "POST", body: JSON.stringify({ customer_id: els.customer.value }) })
      .then(function (res) { if (!res.ok) { throw new Error("login failed"); } return res.json(); })
      .then(function (me) { els.log.textContent = ""; showChat(me); })
      .catch(function () { els.loginError.textContent = "Could not log in. Please try again."; els.loginError.hidden = false; });
  });
  els.guestBtn.addEventListener("click", function () { els.log.textContent = ""; showChat(null); });
  els.logout.addEventListener("click", function () {
    api("/api/logout", { method: "POST" }).then(function () { els.log.textContent = ""; showLogin(); });
  });
  els.form.addEventListener("submit", function (e) { e.preventDefault(); send(els.msg.value); });
  els.msg.addEventListener("input", autosize);
  els.msg.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(els.msg.value); }
  });
  els.chips.addEventListener("click", function (e) {
    if (e.target.classList && e.target.classList.contains("chip")) { send(e.target.textContent); }
  });

  api("/api/me").then(function (res) {
    if (!res.ok) { throw new Error("not logged in"); }
    return res.json();
  }).then(showChat).catch(showLogin);
})();
