// ShellDeck frontend: auth, devices, monitoring, terminal, files, bulk, snippets.
const API = "";
let token = localStorage.getItem("shelldeck_token") || "";
let currentDevices = [];
// Whether the current user may act on (shell / sftp / docker / edit) a device.
function canAccessDevice(d) {
  if (!currentUser) return false;
  if (currentUser.role === "admin") return true;
  if (currentUser.role === "operator") return d.owner_id === currentUser.id;
  return false; // viewers: view-only
}
let currentUser = null;  // { id, username, role, is_admin }

window.addEventListener("error", (e) => {
  const t = document.getElementById("toast");
  if (t) { t.textContent = "JS ERROR: " + (e.message || e.error); t.className = "toast error"; }
});

function authHeaders() {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function showToast(msg, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  setTimeout(() => t.classList.add("hidden"), 2500);
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { ...authHeaders(), ...(opts.headers || {}) },
  });
  if (res.status === 401) { logout(); throw new Error("Unauthorized"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Inline Lucide-style icons (MIT). Kept local so ShellDeck stays $0 / offline. ----
const ICONS = {
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/>',
  folder: '<path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2Z"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  restart: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  kill: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  logs: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  refresh: '<path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16v5h5"/>',
  up: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  folderPlus: '<path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2Z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
  filePlus: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
  home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M9 22V12h6v10"/>',
  drive: '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2Z"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
};
function icon(name, cls = "") {
  const body = ICONS[name] || "";
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

// ----------------------------- Auth flow -----------------------------------
function logout() {
  token = "";
  localStorage.removeItem("shelldeck_token");
  location.href = "/login";
}

async function ensureAuth() {
  if (!token) { location.href = "/login"; return; }
  try {
    const me = await api("/api/auth/me");
    currentUser = me;
    document.getElementById("username").textContent = `${me.username} (${me.role})`;
    // Admin-only UI: Users tab in sidebar.
    document.getElementById("side-users").style.display = me.role === "admin" ? "" : "none";
  } catch (_) {
    location.href = "/login";
  }
}

// ----------------------------- Tabs ----------------------------------------
function switchTab(name) {
  document.querySelectorAll(".side-nav-item").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  ["devices", "files", "bulk", "docker", "snippets", "scheduled", "sessions", "settings", "users", "terminal"].forEach(v => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== name);
  });
  if (name === "files") loadFiles();
  if (name === "bulk") renderBulkDevices();
  if (name === "snippets") loadSnippets();
  if (name === "docker") loadDocker();
  if (name === "users") loadUsers();
  if (name === "scheduled") loadScheduled();
  if (name === "sessions") loadSessions();
  if (name === "settings") loadSettings();
}

// ----------------------------- Devices -------------------------------------
async function loadDevices() {
  currentDevices = await api("/api/devices");
  // Build the tag filter dropdown from all device tags.
  const tagSel = document.getElementById("device-tag-filter");
  if (tagSel) {
    const all = new Set();
    for (const d of currentDevices) (d.tags || "").split(",").forEach(t => { const v = t.trim(); if (v) all.add(v); });
    const cur = tagSel.value;
    tagSel.innerHTML = '<option value="">All tags</option>' + [...all].sort().map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
    tagSel.value = [...all].includes(cur) ? cur : "";
  }
  refreshFilesDeviceSelect();
  refreshDockerDeviceSelect();
  return currentDevices;
}

// ----------------------------- Monitoring ----------------------------------
async function loadStatus() {
  const grid = document.getElementById("status-grid");
  grid.innerHTML = "<p style='color:var(--muted)'>Loading…</p>";
  try {
    const statuses = await api("/api/monitor/status");
    grid.innerHTML = "";
    const q = (document.getElementById("device-search")?.value || "").trim().toLowerCase();
    const tag = (document.getElementById("device-tag-filter")?.value || "").trim().toLowerCase();
    const filtered = statuses.filter(s => {
      if (q && !(s.name.toLowerCase().includes(q) || (s.host || "").toLowerCase().includes(q))) return false;
      if (tag) {
        const dtags = (currentDevices.find(d => d.id === s.id)?.tags || "").toLowerCase().split(",").map(t => t.trim());
        if (!dtags.includes(tag)) return false;
      }
      return true;
    });
    if (!filtered.length) { grid.innerHTML = "<p style='color:var(--muted)'>" + (q ? "No devices match your search." : "No devices yet. Add one from the sidebar.") + "</p>"; return; }
    for (const s of filtered) {
      const card = document.createElement("div");
      card.className = "status-card";
      const cls = s.reachable ? "ok" : "down";
      const canAccess = (d) => currentUser && (currentUser.role === "admin" || (currentUser.role === "operator" && d.owner_id === currentUser.id));
      const bar = (label, pct) => pct == null ? "" : `
        <div class="metric"><span>${label}</span><b>${pct.toFixed(0)}%</b></div>
        <div class="bar ${pct < 60 ? "ok" : pct < 85 ? "warn" : "bad"}"><span style="width:${pct}%"></span></div>`;
      card.innerHTML = `
        <div class="sc-title"><span>${escapeHtml(s.name)} ${s.tailscale ? '<span class="ts-badge" title="Tailscale">TS</span>' : ''}</span><span class="dot ${cls}"></span></div>
        <div class="metric"><span>Host</span><b>${escapeHtml(s.host)}</b></div>
        ${s.reachable ? `
          ${bar("CPU load", s.cpu_load)}
          ${bar("Memory", s.mem_used_pct)}
          ${bar("Disk", s.disk_used_pct)}
          <div class="metric"><span>Uptime</span><b>${escapeHtml(s.uptime || "-")}</b></div>
        ` : `<div class="metric"><span>Status</span><b style="color:var(--danger)">unreachable</b></div>
             <div class="metric"><span></span><b>${escapeHtml(s.message)}</b></div>`}
        ${s.tags ? `<div class="tag-row">${s.tags.split(",").map(t => `<span class="tag">${escapeHtml(t.trim())}</span>`).join("")}</div>` : ""}
        <div class="di-actions" style="margin-top:10px">
          ${canAccess(s) ? `<button class="btn btn-primary btn-icon-text" data-shell="${s.id}" title="Open shell">${icon("terminal")}<span>Shell</span></button>
          <button class="btn btn-ghost btn-icon" data-files="${s.id}" title="File manager">${icon("folder")}</button>
          <button class="btn btn-ghost btn-icon" data-clone="${s.id}" title="Clone device">${icon("copy")}</button>
          <button class="btn btn-ghost btn-icon" data-edit="${s.id}" title="Edit device">${icon("edit")}</button>
          <button class="btn btn-danger btn-icon" data-del="${s.id}" title="Delete device">${icon("trash")}</button>` : `<span class="muted" style="font-size:11px">read-only</span>`}
        </div>`;
      grid.appendChild(card);
      card.querySelector("[data-shell]").onclick = () => openTerminal(s.id, s.name);
      const fb = card.querySelector("[data-files]");
      if (fb) fb.onclick = () => { switchTab("files"); document.getElementById("files-device").value = s.id; loadFiles(); };
      const cb = card.querySelector("[data-clone]");
      if (cb) cb.onclick = () => cloneDevice(s.id);
      const eb = card.querySelector("[data-edit]");
      if (eb) eb.onclick = () => openModal(+eb.dataset.edit);
      const db = card.querySelector("[data-del]");
      if (db) db.onclick = async () => {
        if (!confirm(`Delete device ${s.name}?`)) return;
        await api(`/api/devices/${s.id}`, { method: "DELETE" });
        loadDevices(); loadStatus(); refreshFilesDeviceSelect();
      };
    }
  } catch (e) { grid.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}

// Search box live filter
document.getElementById("device-search")?.addEventListener("input", () => { loadStatus(); });
document.getElementById("device-tag-filter")?.addEventListener("change", () => { loadStatus(); });

// ----------------------------- Device modal --------------------------------
function openModal(id = null) {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  document.getElementById("modal-title").textContent = id ? "Edit Device" : "Add Device";
  document.getElementById("device-id").value = id || "";
  if (!id) document.getElementById("device-form").reset();
  // Populate bastion (jump host) select with other devices.
  const sel = document.getElementById("f-bastion");
  const editingId = id;
  sel.innerHTML = '<option value="">— none —</option>';
  for (const d of currentDevices) {
    if (editingId && String(d.id) === String(editingId)) continue;  // can't be its own bastion
    const o = document.createElement("option");
    o.value = d.id; o.textContent = `${d.name} (${d.host})`;
    sel.appendChild(o);
  }
  if (id) {
    // preselect bastion for editing
    api(`/api/devices/${id}`).then(d => { sel.value = d.bastion_id ? String(d.bastion_id) : ""; document.getElementById("f-tailscale").checked = !!d.tailscale; }).catch(() => {});
  }
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }

async function cloneDevice(id) {
  try {
    const d = await api(`/api/devices/${id}`);
    openModal();  // fresh add form
    document.getElementById("f-name").value = d.name + " (copy)";
    document.getElementById("f-host").value = d.host;
    document.getElementById("f-port").value = d.port;
    document.getElementById("f-username").value = d.username;
    document.getElementById("f-auth").value = d.auth_method;
    document.getElementById("f-auth").dispatchEvent(new Event("change"));
    document.getElementById("f-os").value = d.os || "";
    document.getElementById("f-notes").value = d.notes || "";
    document.getElementById("f-tags").value = d.tags || "";
    document.getElementById("f-tailscale").checked = !!d.tailscale;
    document.getElementById("f-bastion").value = d.bastion_id ? String(d.bastion_id) : "";
    // Note: password/key are NOT returned by the API (security) — user must re-enter.
    showToast("Cloned: enter password/key then Save", "ok");
  } catch (e) { showToast(e.message, "error"); }
}

document.getElementById("add-device").onclick = () => openModal();
document.getElementById("modal-cancel").onclick = closeModal;
document.getElementById("modal-test").onclick = async () => {
  const id = document.getElementById("device-id").value;
  const errEl = document.getElementById("form-error");
  if (errEl) errEl.textContent = "";
  const payload = {
    name: document.getElementById("f-name").value || "test",
    host: document.getElementById("f-host").value,
    port: +document.getElementById("f-port").value || 22,
    username: document.getElementById("f-username").value,
    auth_method: document.getElementById("f-auth").value,
    password: document.getElementById("f-auth").value === "password" ? document.getElementById("f-password").value : "",
    private_key: document.getElementById("f-auth").value === "key" ? document.getElementById("f-key").value : "",
    os: document.getElementById("f-os").value,
    notes: document.getElementById("f-notes").value,
    bastion_id: document.getElementById("f-bastion").value ? +document.getElementById("f-bastion").value : null,
  };
  if (!payload.host) { showToast("Enter host first", "error"); return; }
  try {
    let res;
    if (id) {
      res = await api(`/api/devices/${id}/test`, { method: "GET" });
    } else {
      // Create temporary device, test, then delete it.
      const created = await api("/api/devices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      res = await api(`/api/devices/${created.id}/test`, { method: "GET" });
      await api(`/api/devices/${created.id}`, { method: "DELETE" });
    }
    if (res.ok) showToast("✅ " + res.message, "ok");
    else showToast("❌ " + res.message, "error");
  } catch (e) { showToast(e.message, "error"); }
};

document.getElementById("f-auth").onchange = (e) => {
  const isKey = e.target.value === "key";
  document.getElementById("f-pass-label").classList.toggle("hidden", isKey);
  document.getElementById("f-key-label").classList.toggle("hidden", !isKey);
  document.getElementById("f-genkey").classList.toggle("hidden", !isKey);
  document.getElementById("f-pubkey-label").classList.toggle("hidden", !isKey);
};
document.getElementById("f-genkey").onclick = async () => {
  try {
    const k = await api("/api/devices/generate-key");
    document.getElementById("f-key").value = k.private_key;
    document.getElementById("f-pubkey").value = k.public_key;
    showToast("Keypair generated — copy the public key to the host's authorized_keys", "ok");
  } catch (e) { showToast(e.message, "error"); }
};

document.getElementById("device-form").onsubmit = async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("form-error");
  if (errEl) errEl.textContent = "";
  const id = document.getElementById("device-id").value;
  const payload = {
    name: document.getElementById("f-name").value,
    host: document.getElementById("f-host").value,
    port: +document.getElementById("f-port").value || 22,
    username: document.getElementById("f-username").value,
    auth_method: document.getElementById("f-auth").value,
    password: document.getElementById("f-auth").value === "password" ? document.getElementById("f-password").value : "",
    private_key: document.getElementById("f-auth").value === "key" ? document.getElementById("f-key").value : "",
    os: document.getElementById("f-os").value,
    notes: document.getElementById("f-notes").value,
    tags: document.getElementById("f-tags").value.trim(),
    tailscale: document.getElementById("f-tailscale").checked,
    bastion_id: document.getElementById("f-bastion").value ? +document.getElementById("f-bastion").value : null,
  };
  try {
    if (id) await api(`/api/devices/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    else await api("/api/devices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    closeModal(); loadDevices(); loadStatus();
    showToast("Saved", "ok");
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (errEl) errEl.textContent = "Error: " + msg;
    showToast(msg, "error");
  }
};

// ----------------------------- Export / Import -----------------------------
document.getElementById("export-devices").onclick = async () => {
  try {
    const res = await fetch("/api/devices/export", { headers: authHeaders() });
    if (!res.ok) throw new Error("Export failed (" + res.status + ")");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "shelldeck-devices.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { showToast(e.message, "error"); }
};
function downloadInventory(fmt, filename) {
  fetch(`/api/devices/inventory/${fmt}`, { headers: authHeaders() })
    .then(r => { if (!r.ok) throw new Error("Export failed (" + r.status + ")"); return r.blob(); })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    })
    .catch(e => showToast(e.message, "error"));
}
document.getElementById("inv-ansible").onclick = () => downloadInventory("ansible", "shelldeck-inventory.ini");
document.getElementById("inv-terraform").onclick = () => downloadInventory("terraform", "shelldeck-inventory.tf");
document.getElementById("import-devices").onclick = () => {
  document.getElementById("import-modal").classList.remove("hidden");
};
// Tailscale discovery
document.getElementById("discover-tailscale").onclick = async () => {
  const list = document.getElementById("ts-list");
  const errEl = document.getElementById("ts-error");
  errEl.textContent = "";
  list.innerHTML = "<p class='muted'>Scanning Tailscale network…</p>";
  document.getElementById("ts-modal").classList.remove("hidden");
  try {
    const data = await api("/api/devices/tailscale/discover");
    if (!data.available) {
      list.innerHTML = `<p style='color:var(--danger)'>Tailscale CLI not available on this host.${data.error ? " (" + escapeHtml(data.error) + ")" : ""}</p>`;
      return;
    }
    if (!data.nodes.length) {
      list.innerHTML = "<p class='muted'>No new Tailscale devices found (or all already added).</p>";
      return;
    }
    list.innerHTML = data.nodes.map((n, i) => `
      <div class="ts-node" data-ip="${escapeHtml(n.ip)}" data-name="${escapeHtml(n.name)}">
        <div><b>${escapeHtml(n.name)}</b> ${n.online ? '<span class="dot up"></span>' : '<span class="dot down"></span>'}</div>
        <div class="muted" style="font-size:12px">${escapeHtml(n.ip)}${n.hostname ? " · " + escapeHtml(n.hostname) : ""}${n.os ? " · " + escapeHtml(n.os) : ""}</div>
      </div>`).join("");
    list.querySelectorAll(".ts-node").forEach(el => el.onclick = () => {
      openModal();
      document.getElementById("f-name").value = el.dataset.name;
      document.getElementById("f-host").value = el.dataset.ip;
      document.getElementById("f-tailscale").checked = true;
      document.getElementById("ts-modal").classList.add("hidden");
    });
  } catch (e) { errEl.textContent = "Error: " + e.message; }
};
document.getElementById("ts-cancel").onclick = () => document.getElementById("ts-modal").classList.add("hidden");
document.getElementById("import-cancel").onclick = () => document.getElementById("import-modal").classList.add("hidden");
document.getElementById("import-save").onclick = async () => {
  const errEl = document.getElementById("import-error");
  errEl.textContent = "";
  try {
    const data = JSON.parse(document.getElementById("import-text").value);
    const res = await api("/api/devices/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    document.getElementById("import-modal").classList.add("hidden");
    await loadDevices();
    showToast(`Imported ${res.imported} device(s)`, "ok");
  } catch (err) {
    errEl.textContent = "Error: " + (err.message || err);
  }
};

// ----------------------------- Files (SFTP) --------------------------------
let filesCurrent = { deviceId: null, path: "/" };
function refreshFilesDeviceSelect() {
  const sel = document.getElementById("files-device");
  sel.innerHTML = "";
  const mine = currentDevices.filter(d => canAccessDevice(d));
  if (!mine.length) { sel.innerHTML = '<option value="">— no accessible devices —</option>'; return; }
  for (const d of mine) {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = `${d.name} (${d.host})`;
    sel.appendChild(o);
  }
  const btn = document.getElementById("files-up");
  const mk = document.getElementById("files-mkdir");
  const nf = document.getElementById("files-newfile");
  const list = document.getElementById("files-list");
}
async function loadFiles() {
  const deviceId = +document.getElementById("files-device").value;
  if (!deviceId) { document.getElementById("files-list").innerHTML = "<p class='muted'>Select a device.</p>"; return; }
  filesCurrent.deviceId = deviceId;
  await listFiles(filesCurrent.path);
}
async function listFiles(path) {
  const list = document.getElementById("files-list");
  list.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const entries = await api(`/api/files/${filesCurrent.deviceId}/browse?path=${encodeURIComponent(path)}`);
    filesCurrent.path = path;
    document.getElementById("files-path").textContent = path;
    const rows = entries.map(e => `
      <div class="file-row ${e.is_dir ? "is-dir" : ""}" data-path="${escapeHtml(e.path)}" data-dir="${e.is_dir}">
        <span class="file-ico">${e.is_dir ? icon("folder") : icon("file")}</span>
        <span class="file-name">${escapeHtml(e.name)}</span>
        <span class="file-size">${e.is_dir ? "" : (e.size + " B")}</span>
        <span class="file-acts">
          ${e.is_dir ? "" : `<button class="btn btn-ghost btn-icon-xs" data-edit-file="${escapeHtml(e.path)}" title="Edit file">${icon("edit")}</button>`}
          <button class="btn btn-danger btn-icon-xs" data-del-file="${escapeHtml(e.path)}" title="Delete">${icon("trash")}</button>
        </span>
      </div>`).join("");
    list.innerHTML = rows || "<p class='muted'>Empty folder.</p>";
    list.querySelectorAll(".file-row").forEach(r => {
      r.onclick = (ev) => {
        if (ev.target.closest("[data-del-file]") || ev.target.closest("[data-edit-file]")) return;
        if (r.dataset.dir === "true") listFiles(r.dataset.path);
      };
    });
    list.querySelectorAll("[data-edit-file]").forEach(b => b.onclick = () => openFileEditor(b.dataset.editFile));
    list.querySelectorAll("[data-del-file]").forEach(b => b.onclick = async () => {
      if (!confirm("Delete " + b.dataset.delFile + "?")) return;
      await api(`/api/files/${filesCurrent.deviceId}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: b.dataset.delFile }) });
      listFiles(filesCurrent.path);
    });
  } catch (e) { list.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}
document.getElementById("files-up").onclick = () => {
  const p = filesCurrent.path.replace(/\/$/, "");
  const parent = p.split("/").slice(0, -1).join("/") || "/";
  listFiles(parent === "" ? "/" : parent);
};
document.getElementById("files-mkdir").onclick = async () => {
  const name = prompt("Folder name:");
  if (!name) return;
  const path = (filesCurrent.path.replace(/\/$/, "") + "/" + name);
  await api(`/api/files/${filesCurrent.deviceId}/mkdir`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
  listFiles(filesCurrent.path);
};
document.getElementById("files-newfile").onclick = () => openFileEditor(null);
async function openFileEditor(path) {
  const box = document.getElementById("file-editor");
  box.classList.remove("hidden");
  document.getElementById("file-editor-title").textContent = path ? "Edit: " + path : "New file";
  document.getElementById("file-editor-content").value = "";
  if (path) {
    try {
      const data = await api(`/api/files/${filesCurrent.deviceId}/read`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
      document.getElementById("file-editor-content").value = data.content;
    } catch (e) { showToast(e.message, "error"); }
  }
  document.getElementById("file-editor-save").onclick = async () => {
    const content = document.getElementById("file-editor-content").value;
    const target = path || (filesCurrent.path.replace(/\/$/, "") + "/" + prompt("New file name:"));
    if (!target) return;
    await api(`/api/files/${filesCurrent.deviceId}/write`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: target, content }) });
    box.classList.add("hidden");
    listFiles(filesCurrent.path);
    showToast("Saved", "ok");
  };
  document.getElementById("file-editor-cancel").onclick = () => box.classList.add("hidden");
}

// ----------------------------- Bulk ----------------------------------------
async function renderBulkDevices() {
  const box = document.getElementById("bulk-devices");
  box.innerHTML = "";
  if (!currentDevices.length) { box.innerHTML = "<p class='muted'>No devices.</p>"; return; }
  for (const d of currentDevices) {
    const id = "bulk-" + d.id;
    const lbl = document.createElement("label");
    lbl.className = "bulk-check";
    lbl.innerHTML = `<input type="checkbox" id="${id}" value="${d.id}" checked /> ${escapeHtml(d.name)} <span class="muted">(${escapeHtml(d.host)})</span>`;
    box.appendChild(lbl);
  }
  fillBulkBastion();
}
document.getElementById("bulk-run").onclick = async () => {
  const cmd = document.getElementById("bulk-command").value.trim();
  if (!cmd) { showToast("Enter a command", "error"); return; }
  const ids = [...document.querySelectorAll("#bulk-devices input:checked")].map(c => +c.value);
  if (!ids.length) { showToast("Select at least one device", "error"); return; }
  const res = await api("/api/bulk/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_ids: ids, command: cmd }) });
  const box = document.getElementById("bulk-results");
  list.innerHTML = res.map(r => `
    <div class="bulk-result ${r.reachable ? "ok" : "down"}">
      <div class="br-head"><b>${escapeHtml(r.name)}</b> <span class="muted">${escapeHtml(r.host)}</span> ${r.reachable ? "✅" : "❌"}</div>
      <pre class="br-out">${escapeHtml(r.reachable ? r.output : r.error)}</pre>
    </div>`).join("");
};
// populate bulk bastion select from current devices (called on load + refresh)
function fillBulkBastion() {
  const sel = document.getElementById("bulk-bastion");
  if (!sel) return;
  sel.innerHTML = '<option value="">— none —</option>' + currentDevices.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("");
}
document.getElementById("bulk-apply").onclick = async () => {
  const ids = [...document.querySelectorAll("#bulk-devices input:checked")].map(c => +c.value);
  if (!ids.length) { showToast("Select at least one device", "error"); return; }
  const payload = { device_ids: ids };
  const tags = document.getElementById("bulk-tags").value.trim();
  const os = document.getElementById("bulk-os").value.trim();
  const notes = document.getElementById("bulk-notes").value.trim();
  const bastion = document.getElementById("bulk-bastion").value;
  if (tags) payload.tags = tags;
  if (os) payload.os = os;
  if (notes) payload.notes = notes;
  if (bastion) payload.bastion_id = +bastion;
  if (Object.keys(payload).length === 1) { showToast("Fill at least one field to edit", "error"); return; }
  try {
    const r = await api("/api/devices/bulk", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    showToast(`Updated ${r.updated} device(s)`, "ok");
    loadDevices(); loadStatus();
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("bulk-delete").onclick = async () => {
  const ids = [...document.querySelectorAll("#bulk-devices input:checked")].map(c => +c.value);
  if (!ids.length) { showToast("Select at least one device", "error"); return; }
  if (!confirm(`Delete ${ids.length} device(s)? This also removes their session history.`)) return;
  try {
    const r = await api("/api/devices/bulk", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_ids: ids }) });
    showToast(`Deleted ${r.deleted} device(s)`, "ok");
    loadDevices(); loadStatus();
  } catch (e) { showToast(e.message, "error"); }
};

// ----------------------------- Snippets ------------------------------------
async function loadSnippets() {
  const list = document.getElementById("snippet-list");
  try {
    const snips = await api("/api/snippets");
    if (!snips.length) { list.innerHTML = "<p class='muted'>No snippets yet. Add one to reuse commands quickly.</p>"; return; }
    list.innerHTML = snips.map(s => `
      <div class="snippet-card">
        <div class="sc-name">${escapeHtml(s.name)}</div>
        <pre class="sc-cmd">${escapeHtml(s.command)}</pre>
        <div class="di-actions">
          <button class="btn btn-primary btn-icon-text" data-run="${s.id}" title="Run on a device">${icon("play")}<span>Run</span></button>
          <button class="btn btn-ghost btn-icon" data-edit-snip="${s.id}" title="Edit snippet">${icon("edit")}</button>
          <button class="btn btn-danger btn-icon" data-del-snip="${s.id}" title="Delete snippet">${icon("trash")}</button>
        </div>
      </div>`).join("");
    list.querySelectorAll("[data-run]").forEach(b => b.onclick = () => runSnippetOnDevice(+b.dataset.run, snips));
    list.querySelectorAll("[data-edit-snip]").forEach(b => b.onclick = () => openSnippetModal(+b.dataset.editSnip, snips));
    list.querySelectorAll("[data-del-snip]").forEach(b => b.onclick = async () => {
      if (!confirm("Delete snippet?")) return;
      await api(`/api/snippets/${b.dataset.delSnip}`, { method: "DELETE" });
      loadSnippets();
    });
  } catch (e) { list.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}
function runSnippetOnDevice(snipId, snips) {
  const s = snips.find(x => x.id === snipId);
  if (!currentDevices.length) { showToast("No devices", "error"); return; }
  const devId = +prompt("Run on device id:\n" + currentDevices.map(d => `${d.id}=${d.name}`).join("\n"), currentDevices[0].id);
  if (!devId) return;
  openTerminal(devId, s.name);
  // Send the command once the shell is ready (best-effort small delay).
  setTimeout(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(s.command + "\n"); }, 1500);
}
function openSnippetModal(id = null, snips = []) {
  const modal = document.getElementById("snippet-modal");
  modal.classList.remove("hidden");
  document.getElementById("snippet-title").textContent = id ? "Edit Snippet" : "New Snippet";
  document.getElementById("snippet-id").value = id || "";
  if (id) {
    const s = snips.find(x => x.id === id);
    document.getElementById("s-name").value = s.name;
    document.getElementById("s-command").value = s.command;
  } else {
    document.getElementById("snippet-form").reset();
  }
}
document.getElementById("snippet-add").onclick = () => openSnippetModal();
document.getElementById("snip-export").onclick = async () => {
  try {
    const data = await api("/api/snippets/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "shelldeck-snippets.json";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("snip-import").onclick = () => {
  const txt = prompt("Paste snippets JSON export:");
  if (!txt) return;
  try {
    const data = JSON.parse(txt);
    api("/api/snippets/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      .then(r => { loadSnippets(); showToast(`Imported ${r.imported} snippet(s)`, "ok"); })
      .catch(e => showToast(e.message, "error"));
  } catch (e) { showToast("Invalid JSON", "error"); }
};
document.getElementById("snippet-cancel").onclick = () => document.getElementById("snippet-modal").classList.add("hidden");
document.getElementById("snippet-form").onsubmit = async (e) => {
  e.preventDefault();
  const id = document.getElementById("snippet-id").value;
  const payload = { name: document.getElementById("s-name").value, command: document.getElementById("s-command").value };
  try {
    if (id) await api(`/api/snippets/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    else await api("/api/snippets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    document.getElementById("snippet-modal").classList.add("hidden");
    loadSnippets();
    showToast("Saved", "ok");
  } catch (err) { showToast(err.message, "error"); }
};

// ----------------------------- Docker --------------------------------------
function refreshDockerDeviceSelect() {
  const sel = document.getElementById("docker-device");
  sel.innerHTML = "";
  const mine = currentDevices.filter(d => canAccessDevice(d));
  if (!mine.length) { sel.innerHTML = '<option value="">— no accessible devices —</option>'; return; }
  for (const d of mine) {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = `${d.name} (${d.host})`;
    sel.appendChild(o);
  }
}
async function loadDocker() {
  const deviceId = +document.getElementById("docker-device").value;
  const box = document.getElementById("docker-list");
  if (!deviceId) { box.innerHTML = "<p class='muted'>Select a device with Docker.</p>"; return; }
  box.innerHTML = "<p class='muted'>Loading containers…</p>";
  try {
    const containers = await api(`/api/docker/${deviceId}/containers`);
    if (!containers.length) { box.innerHTML = "<p class='muted'>No containers.</p>"; return; }
    box.innerHTML = `<table class="docker-table"><thead><tr>
      <th>Name</th><th>Image</th><th>State</th><th>Status</th><th>Ports</th><th>Actions</th>
      </tr></thead><tbody>${containers.map(c => `
      <tr class="${c.state === 'running' ? 'up' : 'down'}">
        <td data-label="Name">${escapeHtml(c.name)}</td>
        <td data-label="Image" class="muted">${escapeHtml(c.image)}</td>
        <td data-label="State">${escapeHtml(c.state)}</td>
        <td data-label="Status">${escapeHtml(c.status)}</td>
        <td data-label="Ports" class="muted">${escapeHtml(c.ports || '-')}</td>
        <td data-label="Actions" class="di-actions">
          <button class="btn btn-ghost btn-icon-xs" data-logs="${escapeHtml(c.id)}" title="Logs">${icon("logs")}</button>
          ${c.state === 'running'
            ? `<button class="btn btn-ghost btn-icon-xs" data-act="stop" data-cid="${escapeHtml(c.id)}" title="Stop">${icon("stop")}</button>
               <button class="btn btn-ghost btn-icon-xs" data-act="pause" data-cid="${escapeHtml(c.id)}" title="Pause">${icon("pause")}</button>
               <button class="btn btn-ghost btn-icon-xs danger" data-act="kill" data-cid="${escapeHtml(c.id)}" title="Kill">${icon("kill")}</button>`
            : `<button class="btn btn-ghost btn-icon-xs" data-act="start" data-cid="${escapeHtml(c.id)}" title="Start">${icon("play")}</button>`}
          <button class="btn btn-ghost btn-icon-xs" data-act="restart" data-cid="${escapeHtml(c.id)}" title="Restart">${icon("restart")}</button>
          <button class="btn btn-ghost btn-icon-xs danger" data-act="remove" data-cid="${escapeHtml(c.id)}" title="Remove">${icon("trash")}</button>
          <button class="btn btn-primary btn-icon-xs" data-exec="${escapeHtml(c.id)}" data-name="${escapeHtml(c.name)}" title="Exec (interactive shell)">${icon("terminal")}</button>
        </td>
      </tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("[data-logs]").forEach(b => b.onclick = () => showDockerLogs(deviceId, b.dataset.logs));
    box.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
      await api(`/api/docker/${deviceId}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ container_id: b.dataset.cid, action: b.dataset.act }) });
      loadDocker();
    });
    box.querySelectorAll("[data-exec]").forEach(b => b.onclick = () => {
      // Use `sh` by default: alpine/minimal images (incl. portainer) ship only `sh`,
      // not `bash`. For containers that do have bash, use the free-form Run box.
      openTerminal(deviceId, `docker exec ${b.dataset.name}`, `docker exec -it ${b.dataset.exec} sh`);
    });
  } catch (e) { box.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}
async function showDockerLogs(deviceId, cid) {
  const box = document.getElementById("docker-logs");
  box.classList.remove("hidden");
  document.getElementById("docker-logs-title").textContent = `Logs — ${cid.slice(0, 12)}`;
  document.getElementById("docker-logs-content").textContent = "Loading…";
  try {
    const data = await api(`/api/docker/${deviceId}/logs/${cid}?lines=200`);
    document.getElementById("docker-logs-content").textContent = data.logs || "(empty)";
  } catch (e) { document.getElementById("docker-logs-content").textContent = e.message; }
}
document.getElementById("docker-refresh").onclick = loadDocker;
document.getElementById("docker-device").onchange = loadDocker;
document.getElementById("docker-logs-close").onclick = () => document.getElementById("docker-logs").classList.add("hidden");
document.getElementById("docker-stats").onclick = async () => {
  const deviceId = +document.getElementById("docker-device").value;
  if (!deviceId) return;
  try {
    const data = await api(`/api/docker/${deviceId}/stats`);
    const txt = data.stats.map(r => `${r.name.padEnd(20)} CPU ${r.cpu.padEnd(8)} MEM ${r.mem}`).join("\n") || "(no running containers)";
    const out = document.getElementById("docker-run-output");
    out.classList.remove("hidden");
    out.textContent = "=== docker stats ===\n" + txt;
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("docker-run-btn").onclick = async () => {
  const deviceId = +document.getElementById("docker-device").value;
  const cmd = document.getElementById("docker-cmd").value.trim();
  const pty = document.getElementById("docker-pty").checked;
  if (!deviceId || !cmd) return;
  const out = document.getElementById("docker-run-output");
  out.classList.remove("hidden");
  out.textContent = "Running...";
  try {
    const data = await api(`/api/docker/${deviceId}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: cmd, pty }) });
    out.textContent = `$ ${data.command}${pty ? "  (tty)" : ""}\n${data.stdout}${data.stderr ? "\n[stderr]\n" + data.stderr : ""}\nexit: ${data.exit_status}`;
  } catch (e) { out.textContent = e.message; }
};

// ----------------------------- Users (admin) -------------------------------
async function loadUsers() {
  if (!currentUser || currentUser.role !== "admin") return;
  const box = document.getElementById("user-list");
  box.innerHTML = "<p class='muted'>Loading users…</p>";
  try {
    const users = await api("/api/users");
    box.innerHTML = `<table class="user-table"><thead><tr>
      <th>ID</th><th>Username</th><th>Role</th><th>Created</th><th>Actions</th>
      </tr></thead><tbody>${users.map(u => `
      <tr>
        <td data-label="ID">${u.id}</td>
        <td data-label="Username">${escapeHtml(u.username)}</td>
        <td data-label="Role"><select class="input-sm user-role" data-id="${u.id}">
          ${["viewer", "operator", "admin"].map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
        </select></td>
        <td data-label="Created" class="muted">${new Date(u.created_at).toLocaleDateString()}</td>
        <td data-label="Actions" class="di-actions">
          <button class="btn btn-ghost btn-icon-xs user-edit" data-id="${u.id}" data-name="${escapeHtml(u.username)}" data-role="${u.role}" title="Edit user">${icon("settings")}</button>
          ${u.id === currentUser.id ? "" : `<button class="btn btn-ghost btn-icon-xs danger user-del" data-id="${u.id}" data-name="${escapeHtml(u.username)}" title="Delete user">${icon("trash")}</button>`}
        </td>
      </tr>`).join("")}</tbody></table>`;
    box.querySelectorAll(".user-role").forEach(s => s.onchange = async () => {
      try {
        await api(`/api/users/${s.dataset.id}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: s.value }) });
        showToast("Role updated", "ok");
        loadUsers();
      } catch (e) { showToast(e.message, "error"); loadUsers(); }
    });
    box.querySelectorAll(".user-edit").forEach(b => b.onclick = () => openUserEdit(b.dataset.id, b.dataset.name, b.dataset.role));
    box.querySelectorAll(".user-del").forEach(b => b.onclick = async () => {
      if (!confirm(`Delete user ${b.dataset.name}?`)) return;
      try {
        await api(`/api/users/${b.dataset.id}`, { method: "DELETE" });
        showToast("User deleted", "ok");
        loadUsers();
      } catch (e) { showToast(e.message, "error"); }
    });
  } catch (e) { box.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}
document.getElementById("user-add").onclick = () => document.getElementById("user-form").classList.toggle("hidden");
document.getElementById("user-cancel").onclick = () => document.getElementById("user-form").classList.add("hidden");
document.getElementById("user-save").onclick = async () => {
  const username = document.getElementById("user-username").value.trim();
  const password = document.getElementById("user-password").value;
  const role = document.getElementById("user-role").value;
  if (!username || !password) { showToast("Username & password required", "error"); return; }
  try {
    await api("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, role }) });
    document.getElementById("user-form").classList.add("hidden");
    loadUsers();
  } catch (e) { showToast(e.message, "error"); }
};
function openUserEdit(id, name, role) {
  document.getElementById("edit-user-id").value = id;
  document.getElementById("edit-user-username").value = name;
  document.getElementById("edit-user-password").value = "";
  document.getElementById("edit-user-role").value = role;
  document.getElementById("user-form").classList.add("hidden");
  document.getElementById("user-edit-form").classList.remove("hidden");
}
document.getElementById("edit-user-cancel").onclick = () => document.getElementById("user-edit-form").classList.add("hidden");
document.getElementById("edit-user-save").onclick = async () => {
  const id = document.getElementById("edit-user-id").value;
  const username = document.getElementById("edit-user-username").value.trim();
  const password = document.getElementById("edit-user-password").value;
  const role = document.getElementById("edit-user-role").value;
  const body = { username };
  if (password) body.password = password;
  if (role) body.role = role;
  try {
    await api(`/api/users/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    document.getElementById("user-edit-form").classList.add("hidden");
    showToast("User updated", "ok");
    loadUsers();
  } catch (e) { showToast(e.message, "error"); }
};

// ----------------------------- Terminal ------------------------------------
let term = null, fitAddon = null, ws = null;
function openTerminal(deviceId, title, initialCommand) {
  switchTab("terminal");
  document.getElementById("terminal-title").textContent = `Shell — ${title}`;
  const termEl = document.getElementById("terminal");
  termEl.innerHTML = "";
  term = new Terminal({ cursorBlink: true, theme: { background: "#000000" } });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(termEl);
  fitAddon.fit();

  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/terminal/${deviceId}?token=${token}`);
  ws.onmessage = (e) => term.write(e.data);
  ws.onclose = () => { term.write("\r\n\x1b[31m[session closed]\x1b[0m\r\n"); };
  ws.onopen = () => { if (initialCommand) ws.send(initialCommand + "\r"); };
  term.onData((d) => ws.readyState === WebSocket.OPEN && ws.send(d));
  term.onResize(({ cols, rows }) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(`\x00resize\x00${cols}\x00${rows}`);
  });

  const onResize = () => fitAddon.fit();
  window.addEventListener("resize", onResize);
  document.getElementById("close-terminal").onclick = () => {
    ws.close(); term.dispose(); window.removeEventListener("resize", onResize);
    switchTab("devices");
  };
}

// ----------------------------- Scheduled tasks -----------------------------
async function loadScheduled() {
  const box = document.getElementById("sched-list");
  box.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const tasks = await api("/api/scheduled");
    if (!tasks.length) { box.innerHTML = "<p class='muted'>No scheduled tasks yet.</p>"; return; }
    box.innerHTML = tasks.map(t => `
      <div class="sched-card">
        <div class="sc-name">${escapeHtml(t.name)} ${t.enabled ? "" : "<span class='muted'>(paused)</span>"} ${t.run_once ? "<span class='muted'>· run once</span>" : ""}</div>
        <pre class="sc-cmd">${escapeHtml(t.command)}</pre>
        <div class="muted" style="font-size:12px">Devices: ${t.device_ids.join(", ") || "-"} · ${t.run_once ? "single run" : "every " + t.interval_minutes + "m"} · ${t.run_at ? "at " + new Date(t.run_at).toLocaleString() : (t.next_run ? "next: " + new Date(t.next_run).toLocaleString() : (t.run_once ? "on create" : "-"))}</div>
        ${t.last_output ? `<pre class="sc-out">${escapeHtml(t.last_output)}</pre>` : ""}
        <div class="di-actions">
          <button class="btn btn-primary btn-icon" data-run-now="${t.id}" title="Run now">${icon("play")}</button>
          <button class="btn btn-danger btn-icon" data-del-task="${t.id}" title="Delete task">${icon("trash")}</button>
        </div>
      </div>`).join("");
    box.querySelectorAll("[data-del-task]").forEach(b => b.onclick = async () => {
      if (!confirm("Delete this task?")) return;
      await api(`/api/scheduled/${b.dataset.delTask}`, { method: "DELETE" });
      loadScheduled();
    });
    box.querySelectorAll("[data-run-now]").forEach(b => b.onclick = async () => {
      try { await api(`/api/scheduled/${b.dataset.runNow}/run`, { method: "POST" }); loadScheduled(); showToast("Task triggered", "ok"); }
      catch (e) { showToast(e.message, "error"); }
    });
  } catch (e) { box.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}
document.getElementById("sched-add").onclick = () => {
  const box = document.getElementById("sched-devices");
  if (!currentDevices || !currentDevices.length) { box.innerHTML = "<span class='muted'>No devices yet</span>"; }
  else box.innerHTML = currentDevices.map(d => `
    <label class="chk"><input type="checkbox" value="${d.id}" /> ${escapeHtml(d.name)} <span class="id-badge">#${d.id}</span></label>`).join("");
  document.getElementById("sched-form").classList.remove("hidden");
};
document.getElementById("sched-runonce").onchange = (e) => {
  document.getElementById("sched-runat-label").classList.toggle("hidden", !e.target.checked);
};
document.getElementById("sched-export").onclick = async () => {
  try {
    const data = await api("/api/scheduled/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "shelldeck-tasks.json";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("sched-import").onclick = () => {
  const txt = prompt("Paste scheduled tasks JSON export:");
  if (!txt) return;
  try {
    const data = JSON.parse(txt);
    api("/api/scheduled/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      .then(r => { loadScheduled(); showToast(`Imported ${r.imported} task(s)`, "ok"); })
      .catch(e => showToast(e.message, "error"));
  } catch (e) { showToast("Invalid JSON", "error"); }
};
document.getElementById("sched-cancel").onclick = () => document.getElementById("sched-form").classList.add("hidden");
document.getElementById("sched-save").onclick = async () => {
  const name = document.getElementById("sched-name").value.trim();
  const command = document.getElementById("sched-command").value.trim();
  const ids = Array.from(document.querySelectorAll("#sched-devices input[type=checkbox]:checked")).map(c => parseInt(c.value, 10));
  const interval = parseInt(document.getElementById("sched-interval").value, 10) || 60;
  const run_once = document.getElementById("sched-runonce").checked;
  const run_at_raw = document.getElementById("sched-runat").value;
  // Send as a naive local datetime string (matches the machine's local time, no UTC conversion).
  const run_at = (run_once && run_at_raw) ? run_at_raw : null;
  if (!name || !command) { showToast("Name & command required", "error"); return; }
  try {
    await api("/api/scheduled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, command, device_ids: ids, interval_minutes: interval, run_once, run_at }) });
    document.getElementById("sched-form").classList.add("hidden");
    loadScheduled();
    showToast("Task created", "ok");
  } catch (e) { showToast(e.message, "error"); }
};

// ----------------------------- Settings (notifications) ---------------------
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ""; };
    const setCheck = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    setCheck("set-notify", s.notify_enabled);
    set("set-tg-chat", s.telegram_chat_id);
    set("set-discord", s.discord_webhook);
    set("set-ntfy", s.ntfy_url);
    set("set-gotify", s.gotify_url);
    set("set-slack", s.slack_webhook);
    set("set-webhook", s.webhook_url);
    set("set-email-to", s.email_to);
    set("set-email-host", s.email_host);
    set("set-email-port", s.email_port || 587);
    set("set-email-user", s.email_user);
    set("set-interval", s.monitor_interval);
    setCheck("set-public", s.public_dashboard);
    // Appearance: reflect saved theme.
    const themeSel = document.getElementById("set-theme");
    if (themeSel) {
      let saved = "dark";
      try { saved = localStorage.getItem("shelldeck_theme") || "dark"; } catch (_) {}
      themeSel.value = saved === "light" ? "light" : "dark";
    }
    // Profile: show who is logged in.
    const uEl = document.getElementById("set-username");
    if (uEl && currentUser) uEl.textContent = `${currentUser.username} (${currentUser.role})`;
    // never echo token / password back
    const tok = document.getElementById("set-tg-token"); if (tok) tok.value = "";
    const ep = document.getElementById("set-email-pass"); if (ep) ep.value = "";
  } catch (e) { showToast(e.message, "error"); }
}
document.getElementById("set-save").onclick = async () => {
  if (!currentUser || currentUser.role !== "admin") { showToast("Only admins can change system settings", "error"); return; }
  const payload = {
  notify_enabled: document.getElementById("set-notify").checked,
  telegram_chat_id: document.getElementById("set-tg-chat").value.trim(),
  discord_webhook: document.getElementById("set-discord").value.trim(),
  ntfy_url: document.getElementById("set-ntfy").value.trim(),
  gotify_url: document.getElementById("set-gotify").value.trim(),
  slack_webhook: document.getElementById("set-slack").value.trim(),
  webhook_url: document.getElementById("set-webhook").value.trim(),
  email_to: document.getElementById("set-email-to").value.trim(),
  email_host: document.getElementById("set-email-host").value.trim(),
  email_port: parseInt(document.getElementById("set-email-port").value, 10) || 587,
  email_user: document.getElementById("set-email-user").value.trim(),
  monitor_interval: parseInt(document.getElementById("set-interval").value, 10) || 60,
  public_dashboard: document.getElementById("set-public").checked,
  };
  const tok = document.getElementById("set-tg-token").value.trim();
  if (tok) payload.telegram_token = tok;
  const ep = document.getElementById("set-email-pass").value.trim();
  if (ep) payload.email_password = ep;
  try {
    await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    showToast("Settings saved", "ok");
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("set-test").onclick = async () => {
  const btn = document.getElementById("set-test");
  btn.disabled = true;
  try {
    const r = await api("/api/settings/test", { method: "POST" });
    // Build a readable summary of which channels fired.
    const parts = Object.entries(r).map(([k, v]) => {
      const ok = v === true;
      const skipped = typeof v === "string" && v.startsWith("skipped");
      const icon = ok ? "✅" : (skipped ? "⚪" : "❌");
      return `${icon} ${k}`;
    });
    showToast("Test results:\n" + parts.join("\n"), "ok", 6000);
  } catch (e) {
    showToast("Test failed: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
};
document.getElementById("set-tg-getid").onclick = async () => {
  const btn = document.getElementById("set-tg-getid");
  btn.disabled = true;
  try {
    const r = await api("/api/settings/telegram/chatid", { method: "GET" });
    if (r.ok) {
      document.getElementById("set-tg-chat").value = r.chat_id;
      showToast("Chat ID auto-filled: " + r.chat_id + "\nNow click Save, then Send test.", "ok", 6000);
    } else {
      showToast("Cannot get chat ID: " + r.error, "error", 6000);
    }
  } catch (e) {
    showToast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
};
document.getElementById("set-theme").onchange = () => {
  const t = document.getElementById("set-theme").value === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("shelldeck_theme", t); } catch (_) {}
  showToast("Theme: " + t, "ok");
};
document.getElementById("pw-change").onclick = async () => {
  const oldP = document.getElementById("pw-old").value;
  const newP = document.getElementById("pw-new").value;
  const conf = document.getElementById("pw-conf").value;
  if (!oldP || !newP) { showToast("Enter current and new password", "error"); return; }
  if (newP !== conf) { showToast("New passwords do not match", "error"); return; }
  if (newP.length < 6) { showToast("New password must be at least 6 characters", "error"); return; }
  try {
    await api("/api/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ old_password: oldP, new_password: newP }) });
    document.getElementById("pw-old").value = "";
    document.getElementById("pw-new").value = "";
    document.getElementById("pw-conf").value = "";
    showToast("Password changed", "ok");
  } catch (e) { showToast(e.message, "error"); }
};

// ----------------------------- Session history -----------------------------
async function loadSessions() {
  const box = document.getElementById("session-list");
  box.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const rows = await api("/api/devices/sessions");
    if (!rows.length) { box.innerHTML = "<p class='muted'>No shell sessions recorded yet.</p>"; return; }
    box.innerHTML = `<table class="session-table"><thead><tr>
      <th>Device</th><th>Host</th><th>Started</th><th>Ended</th><th>Duration</th><th>Commands</th>
    </tr></thead><tbody>${rows.map(r => `
      <tr>
        <td data-label="Device">${escapeHtml(r.device_name)}</td>
        <td data-label="Host" class="muted">${escapeHtml(r.device_host)}</td>
        <td data-label="Started">${r.started_at ? new Date(r.started_at).toLocaleString() : "-"}</td>
        <td data-label="Ended">${r.ended_at ? new Date(r.ended_at).toLocaleString() : "active"}</td>
        <td data-label="Duration">${r.duration_s != null ? r.duration_s + "s" : "-"}</td>
        <td data-label="Commands"><button class="btn btn-ghost btn-icon-xs" data-cmds="${r.id}" title="View commands">${icon("list")}</button></td>
      </tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("[data-cmds]").forEach(b => b.onclick = () => {
      const r = rows.find(x => String(x.id) === b.dataset.cmds);
      const cmds = (r.commands || "").split("\n").filter(Boolean);
      const out = document.getElementById("session-cmds");
      out.classList.remove("hidden");
      document.getElementById("session-cmds-title").textContent = `Commands — ${r.device_name}`;
      document.getElementById("session-cmds-body").textContent = cmds.length ? cmds.map(c => "$ " + c).join("\n") : "(no commands recorded)";
    });
  } catch (e) { box.innerHTML = `<p style='color:var(--danger)'>${e.message}</p>`; }
}

// ----------------------------- Boot ----------------------------------------
document.querySelectorAll(".side-nav-item").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
document.getElementById("session-cmds-close").onclick = () => document.getElementById("session-cmds").classList.add("hidden");
document.getElementById("logout").onclick = logout;
document.getElementById("refresh-status").onclick = loadStatus;
document.getElementById("files-device").onchange = loadFiles;

(async () => {
  await ensureAuth();
  if (!token) return;
  await loadDevices();
  await loadStatus();
  refreshFilesDeviceSelect();
  refreshDockerDeviceSelect();
  // Read-only roles (viewer) cannot create/modify anything — hide write buttons.
  if (currentUser && currentUser.role === "viewer") {
    ["add-device", "import-devices", "export-devices", "discover-tailscale", "inv-ansible", "inv-terraform"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = "none";
    });
  }
  // Register service worker for PWA installability.
  if ("serviceWorker" in navigator) {
    try { await navigator.serviceWorker.register("/static/sw.js"); } catch (_) {}
  }
})();
