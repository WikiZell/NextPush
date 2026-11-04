"use strict";
// NextPush admin GUI: plain ES2020, no build step. Everything that comes from the server or from a user is escaped with esc() before it goes into HTML.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const S = {csrf: "", state: null, view: "users", users: [], timer: null};

// ------------------------------------------------------------------------------------------------ plumbing
async function api(method, path, body) {
  const headers = {"Content-Type": "application/json"};
  if (S.csrf) headers["X-CSRF-Token"] = S.csrf;
  let response;
  try { response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin"}); }
  catch (error) { return {ok: false, status: 0, error: "Cannot reach NextPush"}; }
  let data = {};
  try { data = await response.json(); } catch (error) { /* empty body */ }
  if (!response.ok) {
    if (response.status === 401 && S.state && S.state.authenticated) { S.state.authenticated = false; route(); }
    return {ok: false, status: response.status, error: data.error || `Error ${response.status}`};
  }
  return {ok: true, status: response.status, ...data};
}
function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}
async function call(method, path, body) {
  const r = await api(method, path, body);
  if (!r.ok) toast(r.error, "error");
  return r;
}
function modal(html, layer = 30) {
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.style.zIndex = layer;
  overlay.innerHTML = `<div class="modal">${html}</div>`;
  overlay.addEventListener("mousedown", event => { if (event.target === overlay) overlay.remove(); });
  overlay.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => overlay.remove()));
  $("modals").appendChild(overlay);
  return overlay;
}
function ask(message, okLabel = "Yes", danger = false) {
  return new Promise(resolve => {
    const o = modal(`<h2>${esc(message)}</h2><div class="row"><button class="btn ${danger ? "danger" : ""}" id="ask-ok">${esc(okLabel)}</button><button class="btn ghost" data-close>Cancel</button></div>`, 50);
    o.querySelector("#ask-ok").addEventListener("click", () => { o.remove(); resolve(true); });
    o.addEventListener("click", event => { if (event.target.closest("[data-close]")) resolve(false); });
  });
}
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(() => true, () => false);
  const area = document.createElement("textarea");
  area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
  document.body.appendChild(area); area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
  area.remove();
  return Promise.resolve(ok);
}
const fmtDate = ms => ms ? new Date(ms).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "never";
function secretBox(label, value, id) {
  return `<div class="secret"><b>${esc(label)}</b><code id="${id}">${esc(value)}</code><div><button class="btn ghost small" data-copy="${id}">Copy</button></div></div>`;
}
document.addEventListener("click", async event => {
  const b = event.target.closest("[data-copy]");
  if (!b) return;
  const ok = await copyText($(b.dataset.copy).textContent);
  toast(ok ? "Copied" : "Select the text and copy it with Ctrl+C", ok ? "ok" : "error");
});

// ------------------------------------------------------------------------------------------------ screens
function show(which) {
  for (const id of ["boot", "screen-setup", "screen-login", "app"]) $(id).hidden = id !== which;
}
async function route() {
  clearInterval(S.timer);
  const r = await api("GET", "/api/state");
  if (!r.ok) { $("boot").textContent = r.error; show("boot"); return; }
  S.state = r;
  if (r.csrf) S.csrf = r.csrf;
  if (r.setup_required) return show("screen-setup");
  if (!r.authenticated) return show("screen-login");
  show("app");
  $("ver").textContent = `v${r.version}`;
  renderStatus();
  switchView(S.view);
  S.timer = setInterval(() => { if (!document.hidden) { refreshStatus(); if (S.view === "activity") loadActivity(); } }, 5000);
}
function renderStatus() {
  const s = S.state.sftpgo || {};
  const el = $("status");
  el.className = `badge ${s.healthy ? "ok" : "bad"}`;
  el.textContent = s.healthy ? "File service running" : "File service stopped";
  $("banner").innerHTML = s.error ? `<div class="notice bad"><b>The file service (SFTPGo) is not running.</b> ${esc(s.error)}</div>`
    : !S.state.public_url ? `<div class="notice info">The public address is not set yet. Set it in <a href="#" data-goto="settings">Settings</a> so invites know where your server is.</div>` : "";
}
async function refreshStatus() {
  const r = await api("GET", "/api/state");
  if (r.ok) { S.state = r; renderStatus(); }
}
document.addEventListener("click", event => {
  const go = event.target.closest("[data-goto]");
  if (go) { event.preventDefault(); switchView(go.dataset.goto); }
});

$("su-go").addEventListener("click", async () => {
  const err = $("su-error"); err.hidden = true;
  if ($("su-pass").value !== $("su-pass2").value) { err.textContent = "The two passwords are different"; err.hidden = false; return; }
  const r = await api("POST", "/api/setup", {token: $("su-token").value.trim(), password: $("su-pass").value, server_name: $("su-name").value, public_url: $("su-url").value});
  if (!r.ok) { err.textContent = r.error; err.hidden = false; return; }
  S.csrf = r.csrf; route();
});
$("li-go").addEventListener("click", async () => {
  const err = $("li-error"); err.hidden = true;
  const r = await api("POST", "/api/login", {password: $("li-pass").value});
  if (!r.ok) { err.textContent = r.error; err.hidden = false; return; }
  $("li-pass").value = ""; S.csrf = r.csrf; route();
});
$("li-pass").addEventListener("keydown", event => { if (event.key === "Enter") $("li-go").click(); });
$("logout").addEventListener("click", async () => { await api("POST", "/api/logout", {}); S.csrf = ""; route(); });
$("nav").addEventListener("click", event => { const t = event.target.closest("[data-view]"); if (t) switchView(t.dataset.view); });

function switchView(view) {
  S.view = view;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach(v => v.hidden = v.id !== `view-${view}`);
  ({users: loadUsers, activity: loadActivity, settings: loadSettings, log: loadLog})[view]();
}

// ------------------------------------------------------------------------------------------------ users
const MODES = {pull: "Download, and delete after copy (for NextPull)", read: "Download only"};
async function loadUsers() {
  const r = await api("GET", "/api/users");
  const box = $("view-users");
  if (!r.ok) { box.innerHTML = `<div class="notice bad">${esc(r.error)}</div>`; return; }
  S.users = r.users;
  box.innerHTML = `<div class="row between"><div><h1>Users</h1><p class="muted">Each user signs in with NextPull and sees only the folders you share with them.</p></div><button class="btn" id="u-add">+ Add user</button></div>
    <div class="card">${r.users.length ? `<table><thead><tr><th>User</th><th>Shared folders</th><th>Last sign-in</th><th></th></tr></thead><tbody>${r.users.map(u => `
      <tr><td><b>${esc(u.username)}</b> ${u.enabled ? "" : '<span class="badge warn">disabled</span>'}${u.expires ? ` <span class="badge">until ${esc(u.expires)}</span>` : ""}${u.no_block ? ' <span class="badge info" title="never locked out after wrong passwords">no lock-out</span>' : ""}</td>
      <td>${u.folders.map(f => `<span class="badge ${f.mode === "pull" ? "info" : ""}" title="${esc(f.mode_label)}">${esc(f.name)}</span>`).join(" ")}</td><td class="muted">${esc(fmtDate(u.last_login))}</td>
      <td style="text-align:right"><button class="btn ghost small" data-invite="${esc(u.username)}">Invite</button> <button class="btn ghost small" data-edit="${esc(u.username)}">Edit</button></td></tr>`).join("")}</tbody></table>`
      : '<div class="empty">No users yet. Click <b>Add user</b> to create the first one.</div>'}</div>`;
  $("u-add").addEventListener("click", () => openUserEditor(null));
}
$("view-users").addEventListener("click", event => {
  const edit = event.target.closest("[data-edit]"), invite = event.target.closest("[data-invite]");
  if (edit) openUserEditor(S.users.find(u => u.username === edit.dataset.edit));
  if (invite) showInvite(invite.dataset.invite);
});

function folderRow(f = {path: "", name: "", mode: "pull"}) {
  return `<div class="frow" data-frow>
    <label class="path">Folder on this server<div class="row" style="flex-wrap:nowrap"><input data-f="path" value="${esc(f.path)}" placeholder="/data/media/films"><button class="btn ghost" type="button" data-pick>Browse...</button></div></label>
    <label>Name shown to the user<input data-f="name" value="${esc(f.name)}" placeholder="(folder name)"></label>
    <label>Access<select data-f="mode">${Object.entries(MODES).map(([k, v]) => `<option value="${k}" ${f.mode === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
    <button class="btn ghost small" type="button" data-remove title="Remove this folder">&times;</button></div>`;
}
function openUserEditor(user) {
  const isNew = !user;
  user = user || {username: "", folders: [], enabled: true, max_sessions: 4, download_kbps: 0, expires: ""};
  const o = modal(`<div class="modal-head"><h2>${isNew ? "Add user" : `Edit ${esc(user.username)}`}</h2><button class="close" data-close>&times;</button></div>
    <div class="stack">
      ${isNew ? `<div class="cols"><label>User name<input id="ue-name" autocomplete="off" placeholder="bob"><small>lowercase letters, digits, . - _</small></label>
        <label>Password <small>leave empty to generate a strong one</small><input id="ue-pass" type="password" autocomplete="new-password"></label></div>` : ""}
      <h3>Shared folders</h3>
      <div id="ue-folders" class="stack">${(user.folders.length ? user.folders : [undefined]).map(f => folderRow(f)).join("")}</div>
      <div><button class="btn ghost small" id="ue-addfolder" type="button">+ Add another folder</button></div>
      <details><summary><b>Limits</b></summary><div class="cols" style="margin-top:10px">
        <label>Simultaneous connections<input id="ue-sessions" type="number" min="0" max="100" value="${esc(user.max_sessions)}"><small>0 = unlimited</small></label>
        <label>Download speed limit (KB/s)<input id="ue-speed" type="number" min="0" value="${esc(user.download_kbps)}"><small>0 = unlimited</small></label>
        <label>Access ends on<input id="ue-expires" type="date" value="${esc(user.expires)}"><small>empty = never</small></label></div></details>
      ${isNew ? "" : `<label class="row" style="flex-direction:row;gap:8px"><input id="ue-enabled" type="checkbox" ${user.enabled ? "checked" : ""}><span>Account enabled</span></label>`}
      <label class="row" style="flex-direction:row;gap:8px"><input id="ue-noblock" type="checkbox" ${user.no_block ? "checked" : ""}><span>Never lock this user's address out <small>(for someone whose saved password may be wrong for a while; wrong passwords from them are never counted)</small></span></label>
      <div class="row between"><div class="row"><button class="btn" id="ue-save">${isNew ? "Create user" : "Save changes"}</button><button class="btn ghost" data-close>Cancel</button></div>
        ${isNew ? "" : '<div class="row"><button class="btn ghost small" id="ue-reset">Reset password</button><button class="btn danger small" id="ue-delete">Delete user</button></div>'}</div>
    </div>`);
  const folders = o.querySelector("#ue-folders");
  o.querySelector("#ue-addfolder").addEventListener("click", () => folders.insertAdjacentHTML("beforeend", folderRow()));
  o.addEventListener("click", event => {
    const rm = event.target.closest("[data-remove]"), pick = event.target.closest("[data-pick]");
    if (rm) { if (folders.children.length > 1) rm.closest("[data-frow]").remove(); else toast("A user needs at least one folder", "error"); }
    if (pick) { const input = pick.closest("[data-frow]").querySelector('[data-f="path"]'); pickFolder(input.value, p => { input.value = p; }); }
  });
  const collect = () => [...folders.querySelectorAll("[data-frow]")].map(row => ({path: row.querySelector('[data-f="path"]').value.trim(), name: row.querySelector('[data-f="name"]').value.trim(), mode: row.querySelector('[data-f="mode"]').value}));
  const limits = () => ({max_sessions: Number($("ue-sessions").value || 0), download_kbps: Number($("ue-speed").value || 0), expires: $("ue-expires").value});
  o.querySelector("#ue-save").addEventListener("click", async () => {
    const button = o.querySelector("#ue-save"); button.disabled = true;
    if (isNew) {
      const r = await call("POST", "/api/users", {username: $("ue-name").value, password: $("ue-pass").value, folders: collect(), no_block: $("ue-noblock").checked, ...limits()});
      button.disabled = false;
      if (r.ok) { o.remove(); showCreated(r.user.username, r.password); loadUsers(); }
    } else {
      const r = await call("PUT", `/api/users/${encodeURIComponent(user.username)}`, {folders: collect(), enabled: $("ue-enabled").checked, no_block: $("ue-noblock").checked, ...limits()});
      button.disabled = false;
      if (r.ok) { o.remove(); toast("Saved. The user was disconnected so the change applies at once.", "ok"); loadUsers(); }
    }
  });
  if (!isNew) {
    o.querySelector("#ue-reset").addEventListener("click", async () => {
      if (!await ask(`Make a new password for ${user.username}? The old one stops working at once.`, "Reset password", true)) return;
      const r = await call("POST", `/api/users/${encodeURIComponent(user.username)}/password`, {});
      if (r.ok) { o.remove(); showCreated(user.username, r.password, true); }
    });
    o.querySelector("#ue-delete").addEventListener("click", async () => {
      if (!await ask(`Delete ${user.username}? They lose access at once. The shared folders and files are not touched.`, "Delete user", true)) return;
      const r = await call("DELETE", `/api/users/${encodeURIComponent(user.username)}`);
      if (r.ok) { o.remove(); toast("User deleted", "ok"); loadUsers(); }
    });
  }
}
async function showCreated(username, password, isReset = false) {
  const inv = await api("GET", `/api/users/${encodeURIComponent(username)}/invite`);
  const o = modal(`<div class="modal-head"><h2>${isReset ? "New password" : "User created"}</h2><button class="close" data-close>&times;</button></div>
    <p class="muted">${isReset ? "This is the new password of" : "Give these to"} <b>${esc(username)}</b>. <b>The password is shown only now</b> and cannot be read again.</p>
    ${secretBox("Password", password, "sc-pass")}
    ${inv.ok ? secretBox("Invite code for NextPull (Settings -> Other servers -> Add a server)", inv.code, "sc-code") + '<p class="hint">Send the invite code and the password separately: the code does not contain the password.</p>'
      : `<div class="notice info">${esc(inv.error)}</div>`}
    <div class="row"><button class="btn" data-close>Done</button></div>`);
  return o;
}
async function showInvite(username) {
  const r = await call("GET", `/api/users/${encodeURIComponent(username)}/invite`);
  if (!r.ok) return;
  modal(`<div class="modal-head"><h2>Invite for ${esc(username)}</h2><button class="close" data-close>&times;</button></div>
    ${secretBox("Invite code", r.code, "iv-code")}<p class="hint">It holds the server address, the user name and the access key, never the password. In NextPull: Settings -> Other servers -> Add a server -> paste it.</p>
    <div class="row"><button class="btn" data-close>Done</button></div>`);
}

function pickFolder(start, onPick) {
  const o = modal(`<div class="modal-head"><h2>Choose a folder</h2><button class="close" data-close>&times;</button></div><div class="crumbs" id="pf-crumbs"></div><div class="list" id="pf-list"></div>
    <div class="row between"><span class="muted">Selected: <b class="mono" id="pf-sel"></b></span><div class="row"><button class="btn" id="pf-ok">Use this folder</button><button class="btn ghost" data-close>Cancel</button></div></div>`, 40);
  let current = "";
  const load = async path => {
    const r = await call("GET", `/api/fs?path=${encodeURIComponent(path)}`);
    if (!r.ok) return;
    current = r.path;
    $("pf-sel").textContent = current || "(choose a drive or folder)";
    $("pf-crumbs").innerHTML = `<button data-go="">This server</button>` + (r.parent !== null && current ? `<button data-go="${esc(r.parent)}">&larr; Up</button>` : "");
    $("pf-list").innerHTML = r.dirs.map(d => `<div class="item" data-go="${esc(d.path)}">&#128193; <b>${esc(d.name)}</b></div>`).join("") || '<div class="empty">No sub-folders here.</div>';
  };
  o.addEventListener("click", event => { const g = event.target.closest("[data-go]"); if (g) load(g.dataset.go); });
  o.querySelector("#pf-ok").addEventListener("click", () => { if (!current) { toast("Open a folder first", "error"); return; } o.remove(); onPick(current); });
  load(start && start.startsWith("/") || /^[A-Za-z]:/.test(start || "") ? start : "");
}

// ------------------------------------------------------------------------------------------------ activity
async function loadActivity() {
  const [r, blocked] = await Promise.all([api("GET", "/api/activity"), api("GET", "/api/blocked")]);
  const box = $("view-activity");
  box.innerHTML = `<div><h1>Activity</h1><p class="muted">Who is connected right now. Refreshes every few seconds.</p></div><div class="card">${
    !r.ok ? `<div class="notice bad">${esc(r.error)}</div>` : r.connections.length ? `<table><thead><tr><th>User</th><th>From</th><th>Since</th><th>Doing</th></tr></thead><tbody>${r.connections.map(c => `<tr><td><b>${esc(c.username)}</b></td><td class="mono">${esc(c.address)}</td><td class="muted">${esc(fmtDate(c.since))}</td><td class="muted">${esc(c.command)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">Nobody is connected.</div>'}</div>
    <div class="card stack"><h2>Blocked addresses</h2><p class="muted">After 8 wrong passwords from one address, NextPush locks that address out for a short time (seconds, growing to at most 10 minutes). If someone is locked out (for example their saved password was wrong for a while), unblock them here. You can exempt a user in their settings.</p>${
    !blocked.ok ? `<div class="notice bad">${esc(blocked.error)}</div>` : blocked.blocked.length ? `<table><tbody>${blocked.blocked.map(b => `<tr><td class="mono">${esc(b.ip)}</td><td class="muted">locked for ${b.seconds >= 60 ? Math.ceil(b.seconds / 60) + " more minute(s)" : b.seconds + " more second(s)"}</td><td style="text-align:right"><button class="btn ghost small" data-unblock="${esc(b.ip)}">Unblock</button></td></tr>`).join("")}</tbody></table>` : '<div class="empty">No address is blocked.</div>'}</div>`;
  box.querySelectorAll("[data-unblock]").forEach(b => b.addEventListener("click", async () => { const x = await call("DELETE", `/api/blocked/${encodeURIComponent(b.dataset.unblock)}`); if (x.ok) { toast("Unblocked", "ok"); loadActivity(); } }));
}

// ------------------------------------------------------------------------------------------------ settings
async function loadSettings() {
  const r = await api("GET", "/api/settings");
  const box = $("view-settings");
  if (!r.ok) { box.innerHTML = `<div class="notice bad">${esc(r.error)}</div>`; return; }
  const key = r.access_key;
  box.innerHTML = `<div><h1>Settings</h1></div>
    <div class="card stack"><h2>Your server</h2>
      <label>Server name <small>shown to invited people</small><input id="st-name" value="${esc(r.server_name)}" maxlength="60"></label>
      <label>Public address <small>what clients use, for example https://dl.example.com (no path). It is the address of your reverse proxy.</small><input id="st-url" value="${esc(r.public_url)}" placeholder="https://dl.example.com"></label>
      <div><button class="btn" id="st-save">Save</button></div></div>
    <div class="card stack"><h2>Reverse proxy</h2>
      <p>Point your reverse proxy to <b>this computer, port ${esc(r.public_port)}</b> (plain http). Do <b>not</b> publish the admin port (${esc(r.admin_port)}).</p>
      <p class="hint">Turn on HTTPS (Force SSL) in the proxy. Nothing else is needed: the access key and the method filter are built in. Clients get the key in their invite code.</p>
      <label class="row" style="flex-direction:row;gap:8px"><input id="st-keyon" type="checkbox" ${key.enabled ? "checked" : ""}><span>Require the access key <small>(strangers get no answer at all)</small></span></label>
      <div id="st-keybox">${key.enabled ? secretBox(`Access key (header ${key.header})`, key.value, "st-key") : ""}</div>
      <div class="row"><button class="btn ghost small" id="st-rotate">Make a new access key</button><span class="hint">Old invites stop working until you send new ones.</span></div></div>
    <div class="card stack"><h2>Trusted proxies</h2>
      <label>Addresses allowed to tell NextPush the real client address <small>one per line, addresses or networks</small><textarea id="st-proxies">${esc(r.trusted_proxies.join("\n"))}</textarea></label>
      <div><button class="btn ghost" id="st-proxysave">Save</button></div></div>
    <div class="card stack"><h2>Admin password</h2>
      <div class="cols"><label>Current password<input id="pw-cur" type="password" autocomplete="current-password"></label><label>New password<input id="pw-new" type="password" autocomplete="new-password"></label></div>
      <div><button class="btn ghost" id="pw-go">Change password</button></div></div>
    <div class="card stack"><h2>File service</h2><p class="muted">NextPush runs SFTPGo for the file transfers. If something looks stuck, restart it. Users are kept.</p><div><button class="btn ghost" id="st-restart">Restart the file service</button></div></div>`;
  $("st-save").addEventListener("click", async () => { const x = await call("PUT", "/api/settings", {server_name: $("st-name").value, public_url: $("st-url").value}); if (x.ok) { toast("Saved", "ok"); refreshStatus(); } });
  $("st-keyon").addEventListener("change", async event => { const x = await call("PUT", "/api/settings", {access_key_enabled: event.target.checked}); if (x.ok) loadSettings(); });
  $("st-rotate").addEventListener("click", async () => { if (!await ask("Make a new access key? Everyone needs a new invite.", "Make a new key", true)) return; const x = await call("PUT", "/api/settings", {rotate_key: true}); if (x.ok) { toast("New access key", "ok"); loadSettings(); } });
  $("st-proxysave").addEventListener("click", async () => { const x = await call("PUT", "/api/settings", {trusted_proxies: $("st-proxies").value.split("\n").map(v => v.trim()).filter(Boolean)}); if (x.ok) toast("Saved", "ok"); });
  $("pw-go").addEventListener("click", async () => { const x = await call("POST", "/api/admin/password", {current: $("pw-cur").value, new: $("pw-new").value}); if (x.ok) { S.csrf = x.csrf; toast("Password changed", "ok"); $("pw-cur").value = $("pw-new").value = ""; } });
  $("st-restart").addEventListener("click", async () => { const b = $("st-restart"); b.disabled = true; const x = await call("POST", "/api/sftpgo/restart", {}); b.disabled = false; if (x.ok) { toast("Restarted", "ok"); refreshStatus(); } });
}

// ------------------------------------------------------------------------------------------------ log
async function loadLog() {
  const r = await api("GET", "/api/log?lines=200");
  $("view-log").innerHTML = `<div class="row between"><div><h1>Log</h1><p class="muted">The latest lines. Secrets are never written here.</p></div><button class="btn ghost small" id="lg-refresh">Refresh</button></div>
    ${r.ok ? `<div class="card stack"><h2>NextPush</h2><div class="log">${esc(r.nextpush.join("\n")) || "Nothing yet."}</div></div><div class="card stack"><h2>File service (SFTPGo)</h2><div class="log">${esc(r.sftpgo.join("\n")) || "Nothing yet."}</div></div>` : `<div class="notice bad">${esc(r.error)}</div>`}`;
  $("lg-refresh").addEventListener("click", loadLog);
  document.querySelectorAll("#view-log .log").forEach(el => { el.scrollTop = el.scrollHeight; });
}

route();
