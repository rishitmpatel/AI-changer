"use strict";
// AI Switch UI. Talks only to the local backend (same origin, per-run token). All user data is
// inserted with textContent, never as HTML.

const TOKEN = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const $ = (id) => document.getElementById(id);
const state = { providers: [], byId: {}, from: null, to: null, accounts: {}, job: null, timer: null, cardId: null,
                lastRender: "", settings: {}, browser: null, platform: "" };

const ICONS = {
  move: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 7h13l-4-4M17 17H4l4 4"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
};

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}

async function api(path, body) {
  const opts = { headers: { "X-Token": TOKEN, "Content-Type": "application/json" } };
  if (body !== undefined) { opts.method = "POST"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw (data && data.error) || { title: "Something went wrong", message: `HTTP ${r.status}` };
  return data;
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3500);
}

// ------------------------------------------------------------------ navigation
const SCREENS = ["home", "progress", "done", "accounts", "history", "settings"];
function show(name) {
  for (const s of SCREENS) $("screen-" + s).hidden = s !== name;
  const navName = ["progress", "done"].includes(name) ? "home" : name;
  document.querySelectorAll(".nav").forEach((b) => (b.dataset.go === navName ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  if (name === "accounts") loadAccounts();
  if (name === "history") loadHistory();
  if (name === "settings") loadSettings();
  $("main").scrollTop = 0;
}
document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.go === "home" && state.job && !state.job.done) return show("progress");
  if (b.dataset.go === "home" && state.job && state.job.done) return show("done");
  show(b.dataset.go);
}));
document.querySelectorAll("[data-ico]").forEach((el) => (el.innerHTML = ICONS[el.dataset.ico] || ""));

// ------------------------------------------------------------------ home
function tile(p, side) {
  const b = h("button", { class: "tile", type: "button", role: "radio", "aria-checked": "false", "data-id": p.id,
    "data-side": side, title: p.full_name, onclick: () => pick(side, p.id) },
    h("span", { class: "mono", style: `background:${p.color}`, "aria-hidden": "true" }, p.mono, h("span", { class: "dot" })),
    h("span", { class: "nm", text: p.name }));
  b.addEventListener("keydown", (e) => tileKeys(e, side));
  return b;
}
function tileKeys(e, side) {
  const tiles = [...$(side + "-tiles").querySelectorAll(".tile:not(:disabled)")];
  const i = tiles.indexOf(document.activeElement);
  const step = { ArrowRight: 1, ArrowDown: 3, ArrowLeft: -1, ArrowUp: -3 }[e.key];
  if (step === undefined || i < 0) return;
  e.preventDefault();
  const next = tiles[(i + step + tiles.length) % tiles.length];
  next.focus(); pick(side, next.dataset.id);
}
function pick(side, id) {
  if (side === "from") { state.from = id; if (state.to === id) state.to = null; }
  else { state.to = id; if (state.from === id) state.from = null; }
  renderHome();
}
function renderHome() {
  for (const side of ["from", "to"]) {
    const other = side === "from" ? state.to : state.from;
    const mine = state[side];
    $(side + "-tiles").querySelectorAll(".tile").forEach((t) => {
      const id = t.dataset.id;
      t.setAttribute("aria-checked", String(id === mine));
      t.tabIndex = id === mine || (!mine && t === $(side + "-tiles").firstChild) ? 0 : -1;
      t.disabled = id === other;
      t.title = id === other ? `${state.byId[id].name} is already picked on the other side` : state.byId[id].full_name;
      const dot = t.querySelector(".dot");
      dot.className = "dot " + (state.accounts[id] || "");
    });
  }
  const tgt = state.to && state.byId[state.to];
  const chats = $("chats");
  const chatOk = !!(tgt && tgt.chat_import);
  chats.disabled = !chatOk;
  if (!chatOk) chats.checked = false;
  $("chats-why").textContent = !tgt ? "" : chatOk ? `${tgt.name} can import chat history automatically.`
    : `${tgt.name} has no chat-history import, so only memory is moved.`;
  $("chats-wrap").title = chatOk ? "" : (tgt ? `${tgt.name} can't import chat history` : "Pick where to move to first");
  const src = state.from && state.byId[state.from];
  $("export-help").textContent = src ? exportHelp(src.id) : "";
  const ready = state.from && state.to && state.from !== state.to;
  $("start").disabled = !ready;
  $("start-hint").textContent = ready ? `${src.name} → ${tgt.name}` : "Pick a From and a To AI.";
}
function exportHelp(id) {
  return {
    chatgpt: "ChatGPT → Settings → Data controls → Export data. Use the zip from the email.",
    claude: "Claude → Settings → Privacy → Export data. Use the zip from the email.",
    gemini: "takeout.google.com → My Activity → Gemini Apps (JSON).",
    grok: "grok.com → Settings → Data controls → Export account data.",
    copilot: "account.microsoft.com/privacy → Copilot → Export activity history (CSV).",
    meta_ai: "Accounts Center → Download your information (JSON).",
    deepseek: "DeepSeek → Settings → Data → Export data.",
  }[id] || "This AI has no export file; AI Switch will ask it directly.";
}
$("swap").addEventListener("click", () => { [state.from, state.to] = [state.to, state.from]; renderHome(); });
$("pick-export").addEventListener("click", async () => {
  const r = await api("/api/pick-file", {}).catch(() => ({ path: "" }));
  if (r.path) $("export-path").value = r.path; else $("export-path").focus();
});
$("clear-export").addEventListener("click", () => ($("export-path").value = ""));
$("start").addEventListener("click", async () => {
  try {
    await api("/api/move", { source: state.from, target: state.to, chats: $("chats").checked,
                             export_path: $("export-path").value.trim() || null });
    state.cardId = null; state.lastRender = "";
    $("card-area").replaceChildren();
    show("progress"); poll();
  } catch (e) { toast(e.message || e.title); }
});

// ------------------------------------------------------------------ progress
function poll() {
  clearTimeout(state.timer);
  api("/api/job").then((job) => {
    state.job = job;
    if (!job) return;
    renderProgress(job);
    if (job.done) { renderDone(job); show("done"); return; }
    state.timer = setTimeout(poll, 700);
  }).catch(() => (state.timer = setTimeout(poll, 1500)));
}
function renderProgress(job) {
  const src = state.byId[state.from] || {}, dst = state.byId[state.to] || {};
  $("progress-title").textContent = job.kind === "move" && job.steps[0].status === "skipped" ? "Retrying missing facts" : "Moving your memory";
  $("progress-sub").textContent = src.name && dst.name ? `${src.name} → ${dst.name}` : "";
  $("stepper").replaceChildren(...job.steps.map((s, i) => h("li", { class: s.status, "aria-current": s.status === "active" ? "step" : false },
    h("span", { class: "num", text: s.status === "done" ? "✓" : s.status === "error" ? "!" : String(i + 1) }),
    h("span", { text: s.title }))));
  $("bar-fill").style.width = job.progress + "%";
  $("bar").setAttribute("aria-valuenow", job.progress);
  const ul = $("lines");
  if (ul.childElementCount !== job.lines.length) {
    ul.replaceChildren(...job.lines.map((l) => h("li", { text: l })));
    ul.parentElement.scrollTop = ul.parentElement.scrollHeight;
  }
  const card = job.card;
  if (!card) { if (state.cardId !== null) { $("card-area").replaceChildren(); state.cardId = null; } return; }
  if (card.id === state.cardId) return; // don't re-render (keeps the user's edits)
  state.cardId = card.id;
  $("card-area").replaceChildren(renderCard(card));
  const focusable = $("card-area").querySelector("button, input, textarea");
  if (focusable) focusable.focus();
}
async function answer(card, value) {
  await api("/api/job/answer", { card_id: card.id, value });
  $("card-area").replaceChildren(); state.cardId = null;
}
function waiting(text) { return h("span", { class: "waiting" }, h("span", { class: "spinner", "aria-hidden": "true" }), text); }

function renderCard(card) {
  const prov = state.byId[card.provider] || {};
  if (card.kind === "privacy") return renderPrivacy(card);
  if (card.kind === "signin") {
    return h("div", { class: "card", role: "region", "aria-label": card.title },
      h("h3", { text: card.title }), h("p", { text: card.text }),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", type: "button", text: `Sign in to ${prov.name || ""}`,
          onclick: async (e) => {
            e.target.disabled = true;
            await api("/api/accounts/signin", { provider: card.provider });
            e.target.replaceWith(waiting("Waiting for you to sign in in the browser window…"),
              h("button", { class: "btn ghost", type: "button", text: "I'm signed in",
                onclick: (ev) => { ev.target.disabled = true; api("/api/accounts/signin-done", { provider: card.provider }); } }));
          } })));
  }
  if (card.kind === "paste_reply") {
    const ta = h("textarea", { rows: 6, "aria-label": "Reply text" });
    return h("div", { class: "card" }, h("h3", { text: card.title }), h("p", { text: card.text }), ta,
      h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", text: "Continue", onclick: () => answer(card, ta.value) })));
  }
  // paste / challenge / manual
  return h("div", { class: "card", role: "region", "aria-label": card.title || "Your turn" },
    h("h3", { text: card.title || "Your turn" }), h("p", { text: card.text }),
    card.steps ? h("p", { class: "muted small", text: card.steps }) : null,
    h("div", { class: "actions" },
      card.auto ? waiting("I'll continue automatically when it's done…") : null,
      h("button", { class: card.auto ? "btn ghost" : "btn primary", type: "button", text: card.auto ? "I've done it" : "Done",
        onclick: () => answer(card, "done") })));
}

function renderPrivacy(card) {
  const checks = card.findings.map((f) => {
    const cb = h("input", { type: "checkbox", checked: true, "aria-label": `Remove ${f.label} ${f.masked}`, "data-i": f.index });
    const val = h("span", { class: "val", text: f.masked });
    let shown = false;
    const eye = h("button", { class: "eye", type: "button", "aria-label": `Reveal ${f.label}`, "aria-pressed": "false", text: "👁",
      onclick: () => { shown = !shown; val.textContent = shown ? f.value : f.masked; eye.setAttribute("aria-pressed", String(shown)); } });
    return h("li", { class: "finding" }, cb, h("div", {}, val, h("div", { class: "lbl", text: `${f.label} · ${f.places} place${f.places === 1 ? "" : "s"}` })), eye);
  });
  const groups = {};
  for (const c of card.categories) groups[c] = [];
  for (const f of card.facts) (groups[f.category] = groups[f.category] || []).push(f);
  const editors = [];
  const factEl = (f) => {
    const ta = h("textarea", { "aria-label": `Fact in ${f.category}`, rows: 1 }); ta.value = f.text;
    const row = h("div", { class: "fact" }, ta, h("button", { class: "btn ghost del", type: "button", "aria-label": "Delete this fact", text: "✕",
      onclick: () => { row.remove(); editors.splice(editors.indexOf(ed), 1); count(); } }));
    const ed = { f, ta, row }; editors.push(ed); return row;
  };
  const counter = h("span", { class: "muted" });
  const count = () => (counter.textContent = ` (${editors.length} facts)`);
  const preview = h("details", { class: "preview" }, h("summary", {}, "Preview everything that will be sent", counter),
    ...card.categories.filter((c) => groups[c].length).map((c) =>
      h("div", { class: "fact-group" }, h("h4", { text: c }), ...groups[c].map(factEl))));
  count();
  const collect = (removeAll) => ({
    remove: removeAll ? [...$("card-area").querySelectorAll(".finding input:checked")].map((x) => Number(x.dataset.i)) : [],
    facts: editors.map(({ f, ta }) => ({ id: f.id, category: f.category, text: ta.value })),
  });
  const card_ = h("div", { class: "card", role: "region", "aria-label": "Privacy review" },
    h("h3", { text: "Check before sending" }),
    h("p", { class: "muted", text: card.findings.length
      ? `I found ${card.findings.length} personal detail${card.findings.length === 1 ? "" : "s"}. Ticked items will be removed before anything is sent.`
      : "No phone numbers, emails, addresses or ID numbers found." }),
    card.findings.length ? h("ul", { class: "findings" }, ...checks) : null,
    preview,
    h("div", { class: "actions sticky" },
      card.findings.length ? h("button", { class: "btn primary", type: "button", text: "Remove selected & continue", onclick: () => answer(card, collect(true)) }) : null,
      h("button", { class: card.findings.length ? "btn" : "btn primary", type: "button", text: card.findings.length ? "Keep all & continue" : "Continue",
        onclick: () => answer(card, collect(false)) })));
  return card_;
}

$("cancel").addEventListener("click", async () => { await api("/api/job/cancel", {}); toast("Stopping…"); });
$("show-browser").addEventListener("change", (e) => api("/api/browser/visible", { visible: e.target.checked }).catch(() => {}));

// ------------------------------------------------------------------ done
function renderDone(job) {
  const card = $("result-card"), actions = $("done-actions");
  card.classList.remove("bad", "partial");
  actions.replaceChildren();
  $("missing-wrap").hidden = true; $("error-details").hidden = true; $("done-note").textContent = "";
  const btn = (text, cls, fn) => h("button", { class: "btn " + cls, type: "button", text, onclick: fn });
  if (job.error) {
    card.classList.add("bad");
    $("result-icon").textContent = "!";
    $("done-title").textContent = job.error.title;
    $("done-sub").textContent = job.error.message;
    $("error-pre").textContent = job.error.details || "";
    $("error-details").hidden = !job.error.details;
    if (job.error.action === "update_fixes") actions.append(btn("Update fixes", "", async () => { const r = await api("/api/fixes/update", {}); toast(r.summary); }));
    if (job.error.action === "signin") actions.append(btn("Go to Accounts", "", () => show("accounts")));
    if (job.error.action === "download_browser") actions.append(btn("Download Chromium", "", () => show("accounts")));
    actions.append(btn("Try again", "primary", () => $("start").click()), btn("Back", "ghost", moveAnother));
    return;
  }
  const r = job.result;
  const partial = r.missing.length > 0;
  card.classList.toggle("partial", partial);
  $("result-icon").textContent = partial ? "…" : "✓";
  $("done-title").textContent = r.skipped ? "Memory sent" : `Moved ${r.moved} of ${r.total} facts`;
  const dst = state.byId[r.target] || {};
  $("done-sub").textContent = r.skipped ? `Your memory was sent to ${dst.name}. Verification was skipped.`
    : partial ? `${dst.name} doesn't recall everything yet.` : `${dst.name} now knows everything it should.`;
  if (partial) {
    $("missing").replaceChildren(...r.missing.map((m) => h("li", { text: m.text })));
    $("missing-wrap").hidden = false;
    actions.append(btn("Retry missing", "primary", async () => { await api("/api/job/retry", {}); state.cardId = null; show("progress"); poll(); }));
  }
  $("done-note").textContent = r.note || "";
  actions.append(btn("Open output folder", "", () => api("/api/open-folder", { path: r.out_dir })), btn("Move another", partial ? "ghost" : "primary", moveAnother));
}
async function moveAnother() { await api("/api/job/clear", {}); state.job = null; show("home"); }

// ------------------------------------------------------------------ accounts
async function loadAccounts() {
  const list = $("acc-list");
  if (!list.childElementCount) list.replaceChildren(h("p", { class: "muted", text: "Checking sign-ins…" }));
  try {
    const r = await api("/api/accounts");
    state.accounts = r.statuses;
    $("acc-browser").textContent = r.browser ? ` (in ${r.browser.name})` : "";
    $("browser-missing").hidden = !!r.browser;
    list.replaceChildren(...state.providers.map((p) => {
      const st = r.statuses[p.id] || "unknown";
      const label = { signed_in: "Signed in ✓", signed_out: "Not signed in", signing_in: "Waiting for sign-in…", unknown: "Not checked yet" }[st];
      return h("div", { class: "card acc" },
        h("span", { class: "mono", style: `background:${p.color}`, "aria-hidden": "true", text: p.mono }),
        h("div", { class: "who" }, h("div", { text: p.full_name, style: "font-weight:600" }),
          h("div", { class: "st " + st }, h("span", { class: "dot", "aria-hidden": "true" }), label)),
        st === "signed_in" ? null : st === "signing_in"
          ? h("button", { class: "btn", type: "button", text: "I'm signed in",
              onclick: async () => { await api("/api/accounts/signin-done", { provider: p.id }); setTimeout(loadAccounts, 1500); } })
          : h("button", { class: "btn primary", type: "button", text: "Sign in", disabled: !r.browser,
              onclick: async () => { await api("/api/accounts/signin", { provider: p.id }); toast(`Sign in to ${p.name} in the browser window. I'll notice — or just close the window when you're done.`); loadAccounts(); } }));
    }));
    renderHome();
    if (!$("screen-accounts").hidden && Object.values(r.statuses).includes("signing_in")) setTimeout(loadAccounts, 2000);
  } catch (e) { list.replaceChildren(h("p", { class: "muted", text: e.message || "Couldn't check sign-ins." })); }
}
$("download-browser").addEventListener("click", async () => {
  await api("/api/browser/download", {});
  $("dl-bar").hidden = false;
  const tick = async () => {
    const s = await api("/api/state");
    const d = s.download;
    $("dl-fill").style.width = d.total ? Math.round((100 * d.done) / d.total) + "%" : "5%";
    $("dl-text").textContent = d.state === "error" ? d.error : d.state === "done" ? "Ready ✓" : `${Math.round(d.done / 1048576)} MB`;
    if (d.state === "running") setTimeout(tick, 600); else if (d.state === "done") loadAccounts();
  };
  tick();
});

// ------------------------------------------------------------------ history
async function loadHistory() {
  const items = await api("/api/history").catch(() => []);
  const name = (id) => (state.byId[id] || { name: id }).name;
  $("hist-list").replaceChildren(...(items.length ? items.map((it) => h("div", { class: "card hist" },
    h("div", {}, h("div", { style: "font-weight:600", text: `${name(it.source)} → ${name(it.target)}` }),
      h("div", { class: "muted small", text: new Date(it.date).toLocaleString() + (it.kind === "retry" ? " · retry" : "") })),
    h("span", { class: "pill " + it.status, text: it.status === "ok" || it.status === "partial" ? `${it.moved}/${it.total} facts` : it.status }),
    it.out_dir ? h("button", { class: "btn ghost", type: "button", text: "Open", onclick: () => api("/api/open-folder", { path: it.out_dir }) }) : h("span")))
    : [h("p", { class: "muted", text: "No moves yet." })]));
}

// ------------------------------------------------------------------ settings
async function loadSettings() {
  const s = await api("/api/state");
  state.settings = s.settings;
  $("set-output").value = s.settings.output_dir;
  $("set-browser").value = s.settings.browser;
  $("set-lang").value = s.settings.language;
  $("set-browser-found").textContent = s.browser ? `Using ${s.browser.name}.` : "No Edge or Chrome found — see Accounts to download Chromium.";
  $("set-token").placeholder = s.settings.fixes_token ? "saved" : "not set";
}
async function saveSetting(changes) {
  try { await api("/api/settings", changes); toast("Saved"); } catch (e) { toast(e.message || e.title); }
}
$("set-output").addEventListener("change", (e) => saveSetting({ output_dir: e.target.value }));
$("set-browser").addEventListener("change", (e) => saveSetting({ browser: e.target.value }).then(loadSettings));
$("set-lang").addEventListener("change", (e) => saveSetting({ language: e.target.value }));
$("pick-output").addEventListener("click", async () => { const r = await api("/api/pick-folder", {}); if (r.path) { $("set-output").value = r.path; saveSetting({ output_dir: r.path }); } });
$("open-output").addEventListener("click", () => api("/api/open-folder", { path: $("set-output").value }));
$("save-token").addEventListener("click", () => { saveSetting({ fixes_token: $("set-token").value }); $("set-token").value = ""; });
$("update-fixes").addEventListener("click", async (e) => {
  e.target.disabled = true; $("fixes-result").textContent = "Checking…";
  try { const r = await api("/api/fixes/update", {}); $("fixes-result").textContent = r.summary + (Object.keys(r.failed).length ? " — " + Object.values(r.failed)[0] : ""); }
  catch (err) { $("fixes-result").textContent = err.message || "Couldn't download fixes."; }
  e.target.disabled = false;
});
$("reset-signins").addEventListener("click", async () => {
  if (!confirm("Sign out of every AI in AI Switch? You'll need to sign in again before the next move.")) return;
  try { await api("/api/accounts/reset", {}); $("reset-result").textContent = "All sign-ins removed."; state.accounts = {}; renderHome(); }
  catch (e) { $("reset-result").textContent = e.message || e.title; }
});

// ------------------------------------------------------------------ boot
(async function boot() {
  try {
    const s = await api("/api/state");
    state.providers = s.providers; state.platform = s.platform;
    for (const p of s.providers) state.byId[p.id] = p;
    $("version").textContent = "v" + s.version; $("about-version").textContent = "v" + s.version;
    document.title = `AI Switch ${s.version}`;
    $("from-tiles").replaceChildren(...s.providers.map((p) => tile(p, "from")));
    $("to-tiles").replaceChildren(...s.providers.map((p) => tile(p, "to")));
    $("show-browser").checked = !!s.settings.show_browser;
    renderHome();
    if (s.job && !s.job.done) { show("progress"); poll(); }
    api("/api/accounts").then((r) => { state.accounts = r.statuses; renderHome(); }).catch(() => {});
  } catch (e) {
    document.body.replaceChildren(h("div", { class: "card", style: "margin:40px" }, h("h1", { text: "AI Switch couldn't start" }), h("p", { text: e.message || "Please restart the app." })));
  }
})();
