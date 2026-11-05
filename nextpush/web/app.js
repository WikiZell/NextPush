"use strict";
// NextPush admin GUI: plain ES2020, no build step, no inline styles or scripts (strict CSP).
// Everything that comes from the server or from a user is escaped with esc() before it goes into HTML.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const VIEWS = ["overview", "users", "activity", "settings", "log"];
const TITLES = {overview: "Overview", users: "Users", activity: "Activity", settings: "Settings", log: "Log"};
const S = {csrf: "", state: null, view: "overview", users: [], timer: null, check: null, sig: {}, lost: false};
const USERNAME = /^[a-z0-9][a-z0-9._-]{1,31}$/;

// ------------------------------------------------------------------------------------------------ plumbing
async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (S.csrf) headers["X-CSRF-Token"] = S.csrf;
  let response;
  try { response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin"}); }
  catch (error) { return {ok: false, status: 0, error: "Cannot reach NextPush. Is it still running?"}; }
  let data = {};
  try { data = await response.json(); } catch (error) { /* empty body */ }
  if (!response.ok) {
    if (response.status === 401 && S.state && S.state.authenticated) { sessionEnded(); }
    return {ok: false, status: response.status, error: data.error || `Error ${response.status}`};
  }
  return {ok: true, status: response.status, ...data};
}
function sessionEnded() {
  if (!S.state || !S.state.authenticated) return;
  S.state.authenticated = false; S.csrf = "";
  closeAllDialogs();
  clearInterval(S.timer);
  show("screen-login");
  $("li-sub").textContent = "Your session ended. Please sign in again.";
  $("li-pass").focus();
}
function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  const span = document.createElement("span");
  span.textContent = text;
  const close = document.createElement("button");
  close.type = "button"; close.setAttribute("aria-label", "Dismiss"); close.textContent = "×";
  close.addEventListener("click", () => el.remove());
  el.append(span, close);
  $("toasts").appendChild(el);
  raiseToasts();
  while ($("toasts").children.length > 4) $("toasts").firstElementChild.remove();
  setTimeout(() => el.remove(), kind === "error" ? 8000 : 4000);
}
// Toasts live in a popover so they sit in the top layer, above an open dialog (a normal z-index cannot beat a modal).
function raiseToasts() {
  const box = $("toasts");
  if (typeof box.showPopover !== "function") return;
  try { if (box.matches(":popover-open")) box.hidePopover(); box.showPopover(); } catch (error) { /* older browser: toasts stay behind dialogs */ }
}
async function call(method, path, body) {
  const r = await api(method, path, body);
  if (!r.ok && r.status !== 401) toast(r.error, "error");
  return r;
}
async function busy(button, work) {
  if (button.disabled) return undefined;
  button.disabled = true; button.classList.add("busy");
  try { return await work(); } finally { button.disabled = false; button.classList.remove("busy"); }
}
function setError(el, message) {
  el.textContent = message || "";
  el.hidden = !message;
}
function fieldError(input, message) {
  input.setAttribute("aria-invalid", "true");
  const field = input.closest(".field") || input.parentElement;
  let note = field.querySelector(".err");
  if (!note) { note = document.createElement("p"); note.className = "err"; note.id = `${input.id}-err`; field.appendChild(note); }
  note.textContent = message;
  input.setAttribute("aria-describedby", [...new Set([...(input.getAttribute("aria-describedby") || "").split(" ").filter(Boolean), note.id])].join(" "));
  input.focus();
  return false;
}
function clearErrors(root) {
  root.querySelectorAll("[aria-invalid]").forEach(i => {
    i.removeAttribute("aria-invalid");
    const rest = (i.getAttribute("aria-describedby") || "").split(" ").filter(t => t && !t.endsWith("-err")).join(" ");
    if (rest) i.setAttribute("aria-describedby", rest); else i.removeAttribute("aria-describedby");
  });
  root.querySelectorAll(".field .err").forEach(n => n.remove());
}

// ---- dialogs (native <dialog>: focus trap, Esc and inert background come for free)
function closeAllDialogs() { document.querySelectorAll("#modals dialog").forEach(d => d.close()); }
function dialog({title, body, footer = "", size = "", onClose}) {
  const d = document.createElement("dialog");
  d.className = size;
  const id = `dlg-${Math.random().toString(36).slice(2, 8)}`;
  d.setAttribute("aria-labelledby", id);
  d.innerHTML = `<div class="dlg-head"><h2 id="${id}">${esc(title)}</h2><button type="button" class="icon-btn" data-i="x" data-close aria-label="Close"></button></div>
    <div class="dlg-body">${body}</div>${footer ? `<div class="dlg-foot">${footer}</div>` : ""}`;
  d.addEventListener("click", event => {
    if (event.target === d) d.close();
    if (event.target.closest("[data-close]")) d.close();
  });
  // Clean up when closed by our code (d.close()) or by the browser (Esc): do not wait for the "close" event, and never leave a closed dialog (duplicate ids) behind.
  let finished = false;
  const finish = () => { if (finished) return; finished = true; d.remove(); if (onClose) onClose(); };
  const nativeClose = d.close.bind(d);
  d.close = () => { nativeClose(); finish(); };
  d.addEventListener("close", finish);
  d.addEventListener("cancel", () => setTimeout(() => { if (!d.open) finish(); }, 0));
  [...$("modals").children].filter(x => !x.open).forEach(x => x.remove());
  $("modals").appendChild(d);
  d.showModal();
  return d;
}
function ask(title, message, okLabel = "Yes", danger = false) {
  return new Promise(resolve => {
    let answer = false;
    const d = dialog({title, size: "narrow", body: `<p>${esc(message)}</p>`, footer: `<span></span><div class="row"><button type="button" class="btn ghost" data-close>Cancel</button><button type="button" class="btn ${danger ? "danger solid" : "primary"}" id="ask-ok">${esc(okLabel)}</button></div>`, onClose: () => resolve(answer)});
    d.querySelector("#ask-ok").addEventListener("click", () => { answer = true; d.close(); });
    d.querySelector(danger ? "[data-close].btn" : "#ask-ok").focus();
  });
}
// Copies the text of an element. The async clipboard API first; if the browser refuses (no permission, no focus), select the text and use the old command.
async function copyFrom(el) {
  const text = el.textContent;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; } catch (error) { /* fall through */ }
  }
  const selection = window.getSelection(), range = document.createRange();
  range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
  return ok;
}
function when(ms) {
  if (!ms) return "never";
  const diff = Date.now() - ms;
  const rtf = new Intl.RelativeTimeFormat(undefined, {numeric: "auto"});
  const units = [["year", 31536e6], ["month", 2592e6], ["day", 864e5], ["hour", 36e5], ["minute", 6e4]];
  if (diff < 6e4 && diff > -6e4) return "just now";
  for (const [unit, size] of units) if (Math.abs(diff) >= size) return rtf.format(-Math.round(diff / size), unit);
  return "just now";
}
const absDate = ms => ms ? new Date(ms).toLocaleString() : "never";
function secretBox(label, value, id) {
  return `<div class="secret"><span class="label" id="${id}-l">${esc(label)}</span><div class="val"><code id="${id}" aria-labelledby="${id}-l">${esc(value)}</code><button type="button" class="btn small" data-copy="${id}" aria-label="Copy ${esc(label)}">Copy</button></div></div>`;
}
function ico(name) { return `<span class="ico" data-ico="${name}" aria-hidden="true"></span>`; }

document.addEventListener("click", async event => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    const ok = await copyFrom($(copy.dataset.copy));
    toast(ok ? "Copied to the clipboard" : "Select the text and copy it with Ctrl+C", ok ? "ok" : "error");
    return;
  }
  const reveal = event.target.closest("[data-reveal]");
  if (reveal) {
    const input = $(reveal.dataset.reveal), show = input.type === "password";
    input.type = show ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(show));
    reveal.setAttribute("aria-label", show ? "Hide password" : "Show password");
    return;
  }
  const go = event.target.closest("[data-goto]");
  if (go) { event.preventDefault(); location.hash = `#/${go.dataset.goto}`; }
});

// ------------------------------------------------------------------------------------------------ theme and menu
function effectiveTheme() { return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); }
function paintThemeButton() { $("theme-btn").setAttribute("aria-label", effectiveTheme() === "dark" ? "Switch to the light theme" : "Switch to the dark theme"); }
$("theme-btn").addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("nextpush-theme", next); } catch (error) { /* storage blocked */ }
  paintThemeButton();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintThemeButton);
function menu(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menu-btn").setAttribute("aria-expanded", String(open));
  $("menu-btn").setAttribute("aria-label", open ? "Close menu" : "Open menu");
}
$("menu-btn").addEventListener("click", () => menu(!$("sidebar").classList.contains("open")));
$("scrim").addEventListener("click", () => menu(false));
document.addEventListener("keydown", event => { if (event.key === "Escape" && $("sidebar").classList.contains("open") && !document.querySelector("dialog[open]")) { menu(false); $("menu-btn").focus(); } });
matchMedia("(min-width: 861px)").addEventListener("change", event => { if (event.matches) menu(false); });

// ------------------------------------------------------------------------------------------------ screens and routing
function show(which) {
  for (const id of ["boot", "screen-setup", "screen-login", "app"]) $(id).hidden = id !== which;
  const names = {"screen-setup": "Set up", "screen-login": "Sign in", app: TITLES[S.view]};
  document.title = names[which] ? `${names[which]} · NextPush` : "NextPush";
}
async function start() {
  clearInterval(S.timer);
  const r = await api("GET", "/api/state");
  if (!r.ok) {
    $("boot-text").textContent = r.error;
    $("boot").insertAdjacentHTML("beforeend", '<button type="button" class="btn" id="boot-retry">Try again</button>');
    $("boot-retry").addEventListener("click", () => { $("boot-retry").remove(); $("boot-text").textContent = "Loading..."; start(); });
    show("boot");
    return;
  }
  S.state = r; S.lost = false;
  if (r.csrf) S.csrf = r.csrf;
  if (r.setup_required) { show("screen-setup"); $("su-token").focus(); return; }
  if (!r.authenticated) { $("li-sub").textContent = "Enter the admin password."; show("screen-login"); $("li-pass").focus(); return; }
  show("app");
  $("ver").textContent = `v${r.version}`;
  paintThemeButton();
  renderStatus();
  navigate(false);
  S.timer = setInterval(tick, 5000);
}
async function tick() {
  if (document.hidden || !S.state || !S.state.authenticated) return;
  await refreshStatus();
  if (!document.querySelector("dialog[open]")) {
    if (S.view === "activity") loadActivity(true);
    if (S.view === "overview") loadOverview(true);
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
function renderStatus() {
  const s = S.state.sftpgo || {};
  const el = $("status");
  const bad = S.lost || !s.healthy;
  const sig = `${S.lost}|${s.healthy}|${s.error || ""}`;
  if (S.statusSig === sig) return;
  S.statusSig = sig;
  el.className = `badge ${bad ? "bad" : "ok"}`;
  el.innerHTML = `<span class="dot" aria-hidden="true"></span><span class="txt">${S.lost ? "Not reachable" : s.healthy ? "Running" : "Stopped"}</span>`;
  el.title = S.lost ? "The browser cannot reach NextPush" : s.healthy ? "The file service is running" : "The file service is not running";
  let banner = "";
  if (S.lost) banner = '<div class="notice bad" role="alert"><b>Lost contact with NextPush.</b><span>It may be restarting. This page keeps trying.</span></div>';
  else if (s.error) banner = `<div class="notice bad" role="alert"><b>The file service (SFTPGo) is not running.</b><span>${esc(s.error)}</span><span><a href="#/log" data-goto="log">See the log</a> or <a href="#/settings" data-goto="settings">restart it in Settings</a>.</span></div>`;
  $("banner").innerHTML = banner;
}
async function refreshStatus() {
  const r = await api("GET", "/api/state");
  if (r.ok) { S.lost = false; S.state = r; if (r.csrf) S.csrf = r.csrf; renderStatus(); }
  else if (r.status === 0) { S.lost = true; renderStatus(); }
}

$("setup-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget, err = $("su-error");
  setError(err, ""); clearErrors(form);
  if (!$("su-token").value.trim()) return fieldError($("su-token"), "Enter the setup code from the console.");
  if ($("su-pass").value.length < 10) return fieldError($("su-pass"), "Use at least 10 characters.");
  if ($("su-pass").value !== $("su-pass2").value) return fieldError($("su-pass2"), "The two passwords are different.");
  await busy($("su-go"), async () => {
    const r = await api("POST", "/api/setup", {token: $("su-token").value.trim(), password: $("su-pass").value, server_name: $("su-name").value, public_url: $("su-url").value});
    if (!r.ok) { setError(err, r.error); return; }
    S.csrf = r.csrf; form.reset(); S.view = "overview"; location.hash = "#/overview"; start();
  });
});
$("login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const err = $("li-error");
  setError(err, "");
  if (!$("li-pass").value) { setError(err, "Enter the password."); $("li-pass").focus(); return; }
  await busy($("li-go"), async () => {
    const r = await api("POST", "/api/login", {password: $("li-pass").value});
    if (!r.ok) { setError(err, r.error); $("li-pass").select(); return; }
    $("li-pass").value = ""; S.csrf = r.csrf; start();
  });
});
$("logout").addEventListener("click", logout);
$("logout-m").addEventListener("click", logout);
async function logout() { await api("POST", "/api/logout", {}); S.csrf = ""; if (S.state) S.state.authenticated = false; closeAllDialogs(); start(); }

function currentView() {
  const name = (location.hash.match(/^#\/([a-z]+)/) || [])[1];
  return VIEWS.includes(name) ? name : "overview";
}
function navigate(focus = true) {
  const view = currentView();
  S.view = view; menu(false);
  document.querySelectorAll("#sidebar a").forEach(a => { if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${view}`; });
  document.title = `${TITLES[view]} · NextPush`;
  S.sig = {};
  ({overview: loadOverview, users: loadUsers, activity: loadActivity, settings: loadSettings, log: loadLog})[view]().then(() => {
    if (focus) { const h = document.querySelector(`#view-${view} h1`); if (h) { h.tabIndex = -1; h.focus({preventScroll: false}); } }
  });
}
window.addEventListener("hashchange", () => { if (S.state && S.state.authenticated) navigate(true); });

// helpers shared by views
const head = (id, title, sub, actions = "") => `<div class="page-head"><div><h1 id="h-${id}">${esc(title)}</h1>${sub ? `<p class="muted">${sub}</p>` : ""}</div>${actions}</div>`;
const loadingCard = () => '<div class="card stack" aria-busy="true"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
const failure = (message, retry) => `<div class="notice bad" role="alert"><b>${esc(message)}</b>${retry ? `<span><button type="button" class="link-btn" data-retry="${retry}">Try again</button></span>` : ""}</div>`;
// A view renders only when it is still the current one and only when the data changed (so polling never steals focus or flickers).
function paint(view, key, html, quiet) {
  if (S.view !== view) return false;
  if (quiet && S.sig[view] === key) return false;
  S.sig[view] = key;
  $(`view-${view}`).innerHTML = html;
  return true;
}
document.addEventListener("click", event => {
  const retry = event.target.closest("[data-retry]");
  if (retry) navigate(false);
});

// ------------------------------------------------------------------------------------------------ overview
async function loadOverview(quiet = false) {
  if (!quiet && S.view === "overview" && !$("view-overview").firstChild) $("view-overview").innerHTML = loadingCard();
  const [users, activity, settings] = await Promise.all([api("GET", "/api/users"), api("GET", "/api/activity"), api("GET", "/api/settings")]);
  const st = S.state, s = st.sftpgo || {};
  if (!users.ok || !settings.ok) { paint("overview", "err", head("overview", "Overview", "") + failure((users.ok ? settings : users).error, "overview"), false); return; }
  const checkKey = S.check ? `${S.check.level}${S.check.title}` : "";
  const key = JSON.stringify([users.users.map(u => u.username), activity.ok ? activity.connections.length : -1, s.healthy, settings.public_url, settings.server_name, checkKey]);
  const hasUrl = !!settings.public_url, secure = /^https:|^http:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|[^./:]+(:|$))/.test(settings.public_url), checked = !!(S.check && S.check.ok), hasUser = users.users.length > 0;
  const firstOpen = !hasUrl ? 0 : !checked ? 1 : !hasUser ? 2 : -1;
  const step = (i, done, title, text, actions = "") => `<li class="${done ? "done" : ""} ${firstOpen === i ? "next" : ""}"><span class="mark" aria-hidden="true">${done ? "" : i + 1}</span><div class="body"><b>${esc(title)}${done ? '<span class="sr"> (done)</span>' : ""}</b><span>${text}</span>${actions}</div></li>`;
  const result = S.check ? `<div class="notice ${S.check.level}" role="status"><b>${esc(S.check.title)}</b><span>${esc(S.check.detail)}</span></div>` : "";
  const html = head("overview", "Overview", "Is everything ready for your users?") + `
    <div class="grid">
      <div class="card stat"><span class="lbl">File service</span><span class="num">${s.healthy ? "Running" : "Stopped"}</span><span class="badge ${s.healthy ? "ok" : "bad"}"><span class="dot" aria-hidden="true"></span>${s.healthy ? "Healthy" : "Needs attention"}</span></div>
      <div class="card stat"><span class="lbl">Users</span><span class="num">${users.users.length}</span><a href="#/users" data-goto="users">Manage users</a></div>
      <div class="card stat"><span class="lbl">Connected now</span><span class="num">${activity.ok ? activity.connections.length : "-"}</span><a href="#/activity" data-goto="activity">See activity</a></div>
      <div class="card stat"><span class="lbl">Public address</span><span class="num mono txt-sm">${hasUrl ? esc(settings.public_url.replace(/^https?:\/\//, "")) : "Not set"}</span><span class="badge ${hasUrl ? (secure ? "ok" : "warn") : ""}">${hasUrl ? (settings.public_url.startsWith("https") ? "HTTPS" : secure ? "Local network" : "No encryption") : "Add it in Settings"}</span></div>
    </div>
    <section class="card stack" aria-labelledby="h-start"><div><h2 id="h-start">${firstOpen === -1 ? "You are all set" : "Get started"}</h2><p class="muted">${firstOpen === -1 ? "Give each person their invite code (Users, then Invite)." : "Three steps and your first person can download."}</p></div>
      <ol class="steps">
        ${step(0, hasUrl, "Set your public address", `The address of your reverse proxy, for example <span class="mono">https://dl.example.com</span>. Invites carry it.${hasUrl ? ` Now: <span class="mono">${esc(settings.public_url)}</span>` : ""}`, hasUrl ? "" : '<div class="row"><a class="btn primary small" href="#/settings" data-goto="settings">Open Settings</a></div>')}
        ${step(1, checked, "Point your reverse proxy here and check", `Forward the proxy to this computer, port <b>${esc(settings.public_port)}</b> (plain http), with HTTPS turned on. Never publish port ${esc(settings.admin_port)}.`, `${result}<div class="row"><button type="button" class="btn ${firstOpen === 1 ? "primary" : ""} small" id="ov-check" ${hasUrl ? "" : "disabled"}>Check the connection</button>${hasUrl ? "" : '<span class="hint">Set the public address first.</span>'}</div>`)}
        ${step(2, hasUser, "Add your first user", "Choose the folders they can see and send them the invite code.", hasUser ? "" : '<div class="row"><a class="btn primary small" href="#/users" data-goto="users">Add a user</a></div>')}
      </ol></section>`;
  if (paint("overview", key, html, quiet)) { const b = $("ov-check"); if (b) b.addEventListener("click", () => runCheck(b)); }
}
async function runCheck(button) {
  await busy(button, async () => {
    const r = await call("POST", "/api/check-public", {});
    if (r.ok) {
      S.check = r; S.sig = {};
      await (S.view === "settings" ? loadSettings() : loadOverview(false));
      const again = $("st-check") || $("ov-check");
      if (again) again.focus();
    }
  });
}

// ------------------------------------------------------------------------------------------------ users
const MODES = {pull: "Download and delete (NextPull)", read: "Download only"};
const MODE_SHORT = {pull: "download + delete", read: "download only"};
async function loadUsers() {
  if (!$("view-users").firstChild) $("view-users").innerHTML = loadingCard();
  const r = await api("GET", "/api/users");
  if (S.view !== "users") return;
  if (!r.ok) { paint("users", "err", head("users", "Users", "") + failure(r.error, "users"), false); return; }
  S.users = r.users;
  const html = head("users", "Users", "Each user signs in with NextPull and sees only the folders you share with them.", '<button type="button" class="btn primary" id="u-add">' + ico("plus") + "Add user</button>") + `
    <div class="card flush" id="u-card">${r.users.length ? `
      <div class="toolbar"><label class="sr" for="u-search">Search users</label><input id="u-search" type="search" placeholder="Search by name or folder" autocomplete="off"><span class="muted" id="u-count" role="status"></span></div>
      <div class="table-wrap"><table class="stack-table"><thead><tr><th scope="col">User</th><th scope="col">Shared folders</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr">Actions</span></th></tr></thead><tbody id="u-rows"></tbody></table></div>
      <div class="empty" id="u-none" hidden><p>No user matches your search.</p></div>`
      : `<div class="empty"><div class="art">${ico("users")}</div><div><b>No users yet</b><p>Add the first one: pick a name and the folders they may download.</p></div><button type="button" class="btn primary" id="u-add2">Add the first user</button></div>`}</div>`;
  if (!paint("users", "x" + Math.random(), html, false)) return;
  $("u-add").addEventListener("click", () => openUserEditor(null));
  const add2 = $("u-add2"); if (add2) add2.addEventListener("click", () => openUserEditor(null));
  if (r.users.length) { renderUserRows(); $("u-search").addEventListener("input", renderUserRows); }
}
function renderUserRows() {
  const q = ($("u-search").value || "").trim().toLowerCase();
  const rows = S.users.filter(u => !q || u.username.includes(q) || u.folders.some(f => f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)));
  $("u-rows").innerHTML = rows.map(u => `<tr>
    <td data-label="User"><b>${esc(u.username)}</b><div class="chips">${u.enabled ? "" : '<span class="badge warn">disabled</span>'}${u.expires ? `<span class="badge">until ${esc(u.expires)}</span>` : ""}${u.no_block ? '<span class="badge info" title="Never locked out after wrong passwords">no lock-out</span>' : ""}</div></td>
    <td data-label="Shared folders"><div class="chips">${u.folders.map(f => `<span class="badge ${f.mode === "pull" ? "info" : ""}" title="${esc(MODE_SHORT[f.mode] || f.mode)}">${esc(f.name)}</span>`).join("") || '<span class="muted">none</span>'}</div></td>
    <td data-label="Last sign-in" class="muted" title="${esc(absDate(u.last_login))}">${esc(when(u.last_login))}</td>
    <td class="actions"><button type="button" class="btn small" data-invite="${esc(u.username)}" aria-label="Invite code for ${esc(u.username)}">Invite</button> <button type="button" class="btn small" data-edit="${esc(u.username)}" aria-label="Edit ${esc(u.username)}">Edit</button></td></tr>`).join("");
  $("u-none").hidden = rows.length > 0;
  $("u-count").textContent = q ? `${rows.length} of ${S.users.length}` : `${S.users.length} user${S.users.length === 1 ? "" : "s"}`;
}
$("view-users").addEventListener("click", event => {
  const edit = event.target.closest("[data-edit]"), invite = event.target.closest("[data-invite]");
  if (edit) openUserEditor(S.users.find(u => u.username === edit.dataset.edit));
  if (invite) showInvite(invite.dataset.invite);
});

function folderRow(f, index) {
  f = f || {path: "", name: "", mode: "pull"};
  const n = index + 1;
  return `<div class="frow" data-frow>
    <div class="field"><label for="fp-${n}">Folder on this server</label><div class="path-line"><input id="fp-${n}" data-f="path" value="${esc(f.path)}" placeholder="/data/media/films" autocomplete="off" spellcheck="false"><button class="btn" type="button" data-pick>Browse</button></div></div>
    <div class="field"><label for="fn-${n}">Name shown <span class="opt">optional</span></label><input id="fn-${n}" data-f="name" value="${esc(f.name)}" placeholder="(folder name)" autocomplete="off"></div>
    <div class="field"><label for="fm-${n}">Access</label><select id="fm-${n}" data-f="mode">${Object.entries(MODES).map(([k, v]) => `<option value="${k}" ${f.mode === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
    <button class="icon-btn" type="button" data-remove data-i="x" aria-label="Remove folder ${n}"></button></div>`;
}
function openUserEditor(user) {
  const isNew = !user;
  user = user || {username: "", folders: [], enabled: true, max_sessions: 4, download_kbps: 0, expires: ""};
  const body = `<form id="ue-form" class="stack" novalidate>
      ${isNew ? `<div class="cols"><div class="field"><label for="ue-name">User name</label><input id="ue-name" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="32" placeholder="bob" aria-describedby="ue-name-h"><p class="hint" id="ue-name-h">2 to 32 characters: lowercase letters, digits, dot, dash, underscore.</p></div>
        <div class="field"><label for="ue-pass">Password <span class="opt">optional</span></label><div class="pw"><input id="ue-pass" type="password" autocomplete="new-password" aria-describedby="ue-pass-h"><button type="button" class="icon-btn" data-reveal="ue-pass" aria-label="Show password" aria-pressed="false"></button></div><p class="hint" id="ue-pass-h">Leave empty to generate a strong one.</p></div></div>` : ""}
      <fieldset class="stack"><legend><h3>Shared folders</h3></legend>
        <div id="ue-folders" class="stack">${(user.folders.length ? user.folders : [undefined]).map((f, i) => folderRow(f, i)).join("")}</div>
        <div><button class="btn small" id="ue-addfolder" type="button">${ico("plus")}Add another folder</button></div></fieldset>
      <details><summary>Limits</summary><div class="cols">
        <div class="field"><label for="ue-sessions">Simultaneous connections</label><input id="ue-sessions" type="number" min="0" max="100" inputmode="numeric" value="${esc(user.max_sessions)}"><p class="hint">0 = unlimited</p></div>
        <div class="field"><label for="ue-speed">Download speed limit (KB/s)</label><input id="ue-speed" type="number" min="0" inputmode="numeric" value="${esc(user.download_kbps)}"><p class="hint">0 = unlimited</p></div>
        <div class="field"><label for="ue-expires">Access ends on</label><input id="ue-expires" type="date" value="${esc(user.expires)}"><p class="hint">Empty = never</p></div></div></details>
      ${isNew ? "" : `<label class="check"><input id="ue-enabled" type="checkbox" ${user.enabled ? "checked" : ""}><span class="t"><b>Account enabled</b><span>Turn off to block this user without deleting them.</span></span></label>`}
      <label class="check"><input id="ue-noblock" type="checkbox" ${user.no_block ? "checked" : ""}><span class="t"><b>Never lock this user's address out</b><span>For someone whose saved password may be wrong for a while: their wrong passwords are not counted.</span></span></label>
    </form>`;
  const footer = `<div class="row"><button class="btn primary" id="ue-save" type="submit" form="ue-form">${isNew ? "Create user" : "Save changes"}</button><button class="btn ghost" type="button" data-close>Cancel</button></div>
    ${isNew ? "" : '<div class="row"><button class="btn small" type="button" id="ue-reset">Reset password</button><button class="btn danger small" type="button" id="ue-delete">Delete user</button></div>'}`;
  const d = dialog({title: isNew ? "Add user" : `Edit ${user.username}`, size: "wide", body, footer});
  const folders = d.querySelector("#ue-folders");
  let next = folders.children.length;
  d.querySelector("#ue-addfolder").addEventListener("click", () => { folders.insertAdjacentHTML("beforeend", folderRow(undefined, next++)); folders.lastElementChild.querySelector("input").focus(); });
  d.addEventListener("click", event => {
    const rm = event.target.closest("[data-remove]"), pick = event.target.closest("[data-pick]");
    if (rm) { if (folders.children.length > 1) { const row = rm.closest("[data-frow]"); row.remove(); } else toast("A user needs at least one folder", "error"); }
    if (pick) { const input = pick.closest("[data-frow]").querySelector('[data-f="path"]'); pickFolder(input.value, p => { input.value = p; input.focus(); }); }
  });
  const collect = () => [...folders.querySelectorAll("[data-frow]")].map(row => ({path: row.querySelector('[data-f="path"]').value.trim(), name: row.querySelector('[data-f="name"]').value.trim(), mode: row.querySelector('[data-f="mode"]').value}));
  const limits = () => ({max_sessions: Number(d.querySelector("#ue-sessions").value || 0), download_kbps: Number(d.querySelector("#ue-speed").value || 0), expires: d.querySelector("#ue-expires").value});
  const form = d.querySelector("#ue-form");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    clearErrors(form);
    const name = isNew ? d.querySelector("#ue-name").value.trim() : user.username;
    if (isNew && !USERNAME.test(name)) return fieldError(d.querySelector("#ue-name"), "Use 2 to 32 lowercase letters, digits, dots, dashes or underscores, starting with a letter or digit.");
    const list = collect();
    for (const row of folders.querySelectorAll("[data-frow]")) {
      const input = row.querySelector('[data-f="path"]');
      if (!input.value.trim()) return fieldError(input, "Choose a folder, or remove this row.");
    }
    const save = d.querySelector("#ue-save");
    await busy(save, async () => {
      const noBlock = d.querySelector("#ue-noblock").checked;
      if (isNew) {
        const r = await call("POST", "/api/users", {username: name, password: d.querySelector("#ue-pass").value, folders: list, no_block: noBlock, ...limits()});
        if (r.ok) { d.close(); showCreated(r.user.username, r.password); S.sig = {}; loadUsers(); }
      } else {
        const r = await call("PUT", `/api/users/${encodeURIComponent(user.username)}`, {folders: list, enabled: d.querySelector("#ue-enabled").checked, no_block: noBlock, ...limits()});
        if (r.ok) { d.close(); toast("Saved. The user was disconnected so the change applies at once.", "ok"); loadUsers(); }
      }
    });
  });
  if (!isNew) {
    d.querySelector("#ue-reset").addEventListener("click", async () => {
      if (!await ask("Reset password?", `Make a new password for ${user.username}. The old one stops working at once.`, "Reset password", true)) return;
      const r = await call("POST", `/api/users/${encodeURIComponent(user.username)}/password`, {});
      if (r.ok) { d.close(); showCreated(user.username, r.password, true); }
    });
    d.querySelector("#ue-delete").addEventListener("click", async () => {
      if (!await ask(`Delete ${user.username}?`, "They lose access at once. The shared folders and their files are not touched.", "Delete user", true)) return;
      const r = await call("DELETE", `/api/users/${encodeURIComponent(user.username)}`);
      if (r.ok) { d.close(); toast("User deleted", "ok"); loadUsers(); }
    });
  }
  d.querySelector(isNew ? "#ue-name" : "input").focus();
}
async function showCreated(username, password, isReset = false) {
  const inv = await api("GET", `/api/users/${encodeURIComponent(username)}/invite`);
  const created = dialog({title: isReset ? "New password" : "User created", size: "wide", body: `
    <div class="notice warn"><b>Copy the password now.</b><span>It is shown only once and cannot be read again.</span></div>
    <p class="muted">${isReset ? "This is the new password of" : "Give these to"} <b>${esc(username)}</b>.</p>
    ${secretBox("Password", password, "sc-pass")}
    ${inv.ok ? secretBox("Invite code for NextPull", inv.code, "sc-code") + '<p class="hint">In NextPull: Settings, Other servers, Add a server, then paste the code. Send the code and the password separately: the code does not contain the password.</p>'
      : `<div class="notice info"><span>${esc(inv.error)}</span></div>`}`, footer: '<span></span><button type="button" class="btn primary" data-close>Done</button>'});
  created.querySelector(".dlg-foot .btn").focus();
}
async function showInvite(username) {
  const r = await call("GET", `/api/users/${encodeURIComponent(username)}/invite`);
  if (!r.ok) return;
  dialog({title: `Invite for ${username}`, size: "wide", body: `${secretBox("Invite code", r.code, "iv-code")}
    <p class="hint">It holds the server address, the user name and the access key, never the password. In NextPull: Settings, Other servers, Add a server, then paste it.</p>`, footer: '<span></span><button type="button" class="btn primary" data-close>Done</button>'});
}

function pickFolder(start, onPick) {
  let current = "";
  const d = dialog({title: "Choose a folder", size: "wide", body: '<div class="crumbs" id="pf-crumbs"></div><div class="dirlist" id="pf-list" role="group" aria-label="Folders"></div>',
    footer: '<span class="muted">Selected: <b class="mono" id="pf-sel"></b></span><div class="row"><button type="button" class="btn ghost" data-close>Cancel</button><button type="button" class="btn primary" id="pf-ok">Use this folder</button></div>'});
  const load = async path => {
    const r = await call("GET", `/api/fs?path=${encodeURIComponent(path)}`);
    if (!r.ok) return;
    current = r.path;
    $("pf-sel").textContent = current || "(open a drive or folder)";
    $("pf-crumbs").innerHTML = `<button type="button" class="btn small" data-go="">This server</button>${r.parent !== null && current ? `<button type="button" class="btn small" data-go="${esc(r.parent)}">&larr; Up</button>` : ""}`;
    $("pf-list").innerHTML = r.dirs.map(x => `<button type="button" data-go="${esc(x.path)}">${ico("folder")}<b>${esc(x.name)}</b></button>`).join("") || '<div class="empty">No sub-folders here.</div>';
    $("pf-ok").disabled = !current;
  };
  d.addEventListener("click", event => { const g = event.target.closest("[data-go]"); if (g) load(g.dataset.go); });
  d.querySelector("#pf-ok").addEventListener("click", () => { if (current) { d.close(); onPick(current); } });
  load(start && (start.startsWith("/") || /^[A-Za-z]:/.test(start)) ? start : "");
}

// ------------------------------------------------------------------------------------------------ activity
async function loadActivity(quiet = false) {
  if (!quiet && !$("view-activity").firstChild) $("view-activity").innerHTML = loadingCard();
  const [r, blocked] = await Promise.all([api("GET", "/api/activity"), api("GET", "/api/blocked")]);
  const key = JSON.stringify([r.ok ? r.connections.map(c => [c.username, c.address, c.since, c.command]) : r.error, blocked.ok ? blocked.blocked.map(b => b.ip) : blocked.error]);
  const connections = !r.ok ? failure(r.error, "activity") : r.connections.length
    ? `<div class="table-wrap"><table class="stack-table"><thead><tr><th scope="col">User</th><th scope="col">From</th><th scope="col">Since</th><th scope="col">Doing</th></tr></thead><tbody>${r.connections.map(c => `<tr><td data-label="User"><b>${esc(c.username)}</b></td><td data-label="From" class="mono">${esc(c.address)}</td><td data-label="Since" class="muted" title="${esc(absDate(c.since))}">${esc(when(c.since))}</td><td data-label="Doing" class="muted">${esc(c.command || "-")}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty"><div class="art">${ico("pulse")}</div><p>Nobody is connected right now.</p></div>`;
  const locks = !blocked.ok ? failure(blocked.error, "activity") : blocked.blocked.length
    ? `<div class="table-wrap"><table class="stack-table"><thead><tr><th scope="col">Address</th><th scope="col">Locked</th><th scope="col"><span class="sr">Actions</span></th></tr></thead><tbody>${blocked.blocked.map(b => `<tr><td data-label="Address" class="mono">${esc(b.ip)}</td><td data-label="Locked" class="muted">${b.seconds >= 60 ? `${Math.ceil(b.seconds / 60)} more minute(s)` : `${b.seconds} more second(s)`}</td><td class="actions"><button type="button" class="btn small" data-unblock="${esc(b.ip)}" aria-label="Unblock ${esc(b.ip)}">Unblock</button></td></tr>`).join("")}</tbody></table></div>`
    : '<div class="empty"><p>No address is blocked.</p></div>';
  const html = head("activity", "Activity", "Who is connected right now. This page refreshes by itself.") + `
    <section class="card flush" aria-labelledby="h-conn"><div class="toolbar"><h2 id="h-conn">Connected now</h2></div>${connections}</section>
    <section class="card flush" aria-labelledby="h-lock"><div class="toolbar"><div><h2 id="h-lock">Blocked addresses</h2><p class="hint">After 8 wrong passwords from one address, NextPush locks it out for a short time (seconds, growing to at most 10 minutes). Unblock someone here, or exempt a user in their settings.</p></div></div>${locks}</section>`;
  paint("activity", key, html, quiet);
}
$("view-activity").addEventListener("click", async event => {
  const b = event.target.closest("[data-unblock]");
  if (!b) return;
  await busy(b, async () => { const x = await call("DELETE", `/api/blocked/${encodeURIComponent(b.dataset.unblock)}`); if (x.ok) { toast("Unblocked", "ok"); S.sig = {}; loadActivity(); } });
});

// ------------------------------------------------------------------------------------------------ settings
async function loadSettings() {
  if (!$("view-settings").firstChild) $("view-settings").innerHTML = loadingCard();
  const r = await api("GET", "/api/settings");
  if (S.view !== "settings") return;
  if (!r.ok) { paint("settings", "err", head("settings", "Settings", "") + failure(r.error, "settings"), false); return; }
  const key = r.access_key;
  const result = S.check ? `<div class="notice ${S.check.level}" role="status"><b>${esc(S.check.title)}</b><span>${esc(S.check.detail)}</span></div>` : "";
  const html = head("settings", "Settings", "") + `
    <form class="card stack" id="f-server" novalidate aria-labelledby="h-server"><h2 id="h-server">Your server</h2>
      <div class="field"><label for="st-name">Server name</label><input id="st-name" value="${esc(r.server_name)}" maxlength="60" autocomplete="off" aria-describedby="st-name-h"><p class="hint" id="st-name-h">Shown to the people you invite.</p></div>
      <div class="field"><label for="st-url">Public address</label><input id="st-url" value="${esc(r.public_url)}" placeholder="https://dl.example.com" inputmode="url" autocomplete="off" spellcheck="false" autocapitalize="off" aria-describedby="st-url-h"><p class="hint" id="st-url-h">What clients use: the address of your reverse proxy, without a path.</p></div>
      <div><button class="btn primary" type="submit" id="st-save">Save</button></div></form>
    <section class="card stack" aria-labelledby="h-proxy"><h2 id="h-proxy">Reverse proxy</h2>
      <p>Point your reverse proxy to <b>this computer, port ${esc(r.public_port)}</b> (plain http) and turn on HTTPS. Do <b>not</b> publish the admin port (${esc(r.admin_port)}).</p>
      <div class="row"><button type="button" class="btn" id="st-check" ${r.public_url ? "" : "disabled"}>Check the connection</button>${r.public_url ? "" : '<span class="hint">Save a public address first.</span>'}</div>${result}
      <label class="check"><input id="st-keyon" type="checkbox" ${key.enabled ? "checked" : ""}><span class="t"><b>Require the access key</b><span>Strangers get no answer at all. Clients get the key in their invite code.</span></span></label>
      ${key.enabled ? secretBox(`Access key (header ${key.header})`, key.value, "st-key") : ""}
      <div class="row"><button type="button" class="btn small" id="st-rotate">Make a new access key</button><span class="hint">Old invites stop working until you send new ones.</span></div></section>
    <form class="card stack" id="f-proxies" novalidate aria-labelledby="h-trusted"><h2 id="h-trusted">Trusted proxies</h2>
      <div class="field"><label for="st-proxies">Addresses allowed to tell NextPush the real client address</label><textarea id="st-proxies" spellcheck="false" aria-describedby="st-proxies-h">${esc(r.trusted_proxies.join("\n"))}</textarea><p class="hint" id="st-proxies-h">One per line: addresses or networks like 192.168.1.0/24. Needed so the lock-out counts the real person, not your proxy.</p></div>
      <div><button class="btn" type="submit" id="st-proxysave">Save</button></div></form>
    <form class="card stack" id="f-pw" novalidate aria-labelledby="h-pw"><h2 id="h-pw">Admin password</h2>
      <input type="text" value="admin" autocomplete="username" hidden>
      <div class="cols"><div class="field"><label for="pw-cur">Current password</label><input id="pw-cur" type="password" autocomplete="current-password"></div>
        <div class="field"><label for="pw-new">New password</label><input id="pw-new" type="password" autocomplete="new-password" aria-describedby="pw-new-h"><p class="hint" id="pw-new-h">At least 10 characters.</p></div></div>
      <div><button class="btn" type="submit" id="pw-go">Change password</button></div></form>
    <section class="card stack" aria-labelledby="h-svc"><h2 id="h-svc">File service</h2><p class="muted">NextPush runs SFTPGo for the file transfers. If something looks stuck, restart it. Users are kept.</p><div><button type="button" class="btn" id="st-restart">Restart the file service</button></div></section>`;
  if (!paint("settings", "x" + Math.random(), html, false)) return;
  $("f-server").addEventListener("submit", async event => {
    event.preventDefault(); clearErrors($("f-server"));
    await busy($("st-save"), async () => {
      const x = await call("PUT", "/api/settings", {server_name: $("st-name").value, public_url: $("st-url").value});
      if (x.ok) { toast("Saved", "ok"); if (x.public_url !== r.public_url) S.check = null; S.state.public_url = x.public_url; S.state.server_name = x.server_name; await loadSettings(); $("st-save").focus(); }
      else if (/address/i.test(x.error)) fieldError($("st-url"), x.error);
    });
  });
  const check = $("st-check"); check.addEventListener("click", () => runCheck(check));
  $("st-keyon").addEventListener("change", async event => {
    if (!event.target.checked && !await ask("Turn the access key off?", "Anyone who finds your address could try to sign in. Keep it on unless you have a reason.", "Turn off", true)) { event.target.checked = true; return; }
    const x = await call("PUT", "/api/settings", {access_key_enabled: event.target.checked});
    if (x.ok) { S.check = null; await loadSettings(); $("st-keyon").focus(); } else event.target.checked = !event.target.checked;
  });
  $("st-rotate").addEventListener("click", async () => {
    if (!await ask("Make a new access key?", "Everyone needs a new invite code afterwards.", "Make a new key", true)) return;
    const x = await call("PUT", "/api/settings", {rotate_key: true});
    if (x.ok) { toast("New access key", "ok"); S.check = null; await loadSettings(); $("st-rotate").focus(); }
  });
  $("f-proxies").addEventListener("submit", async event => {
    event.preventDefault();
    await busy($("st-proxysave"), async () => { const x = await call("PUT", "/api/settings", {trusted_proxies: $("st-proxies").value.split("\n").map(v => v.trim()).filter(Boolean)}); if (x.ok) toast("Saved", "ok"); });
  });
  $("f-pw").addEventListener("submit", async event => {
    event.preventDefault(); clearErrors($("f-pw"));
    if (!$("pw-cur").value) return fieldError($("pw-cur"), "Enter your current password.");
    if ($("pw-new").value.length < 10) return fieldError($("pw-new"), "Use at least 10 characters.");
    await busy($("pw-go"), async () => {
      const x = await call("POST", "/api/admin/password", {current: $("pw-cur").value, new: $("pw-new").value});
      if (x.ok) { S.csrf = x.csrf; toast("Password changed", "ok"); $("pw-cur").value = $("pw-new").value = ""; }
    });
  });
  $("st-restart").addEventListener("click", async () => {
    if (!await ask("Restart the file service?", "Downloads in progress are interrupted and clients reconnect by themselves.", "Restart")) return;
    await busy($("st-restart"), async () => { const x = await call("POST", "/api/sftpgo/restart", {}); if (x.ok) { toast("Restarted", "ok"); refreshStatus(); } });
  });
}

// ------------------------------------------------------------------------------------------------ log
async function loadLog() {
  if (!$("view-log").firstChild) $("view-log").innerHTML = loadingCard();
  const r = await api("GET", "/api/log?lines=200");
  if (S.view !== "log") return;
  const block = (id, title, lines) => `<section class="card stack" aria-labelledby="${id}-h"><h2 id="${id}-h">${esc(title)}</h2><pre class="log" tabindex="0" aria-labelledby="${id}-h">${esc(lines.join("\n")) || "Nothing yet."}</pre></section>`;
  const html = head("log", "Log", "The latest lines. Secrets are never written here.", '<button type="button" class="btn" id="lg-refresh">Refresh</button>') + (r.ok ? block("lg-np", "NextPush", r.nextpush) + block("lg-sg", "File service (SFTPGo)", r.sftpgo) : failure(r.error, "log"));
  if (!paint("log", "x" + Math.random(), html, false)) return;
  $("lg-refresh").addEventListener("click", () => busy($("lg-refresh"), loadLog));
  document.querySelectorAll("#view-log .log").forEach(el => { el.scrollTop = el.scrollHeight; });
}

paintThemeButton();
start();
