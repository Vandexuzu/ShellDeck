// ShellDeck frontend: auth, devices, monitoring, terminal, files, bulk, snippets.
const API = "";
let token = localStorage.getItem("shelldeck_token") || "";
let currentDevices = [];
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
  ["devices", "files", "bulk", "docker", "snippets", "scheduled", "settings", "users", "terminal"].forEach(v => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== name);
  });
  if (name === "files") loadFiles();
  if (name === "bulk") renderBulkDevices();
  if (name === "snippets") loadSnippets();
  if (name === "docker") loadDocker();
  if (name === "users") loadUsers();
  if (name === "scheduled") loadScheduled();
  if (name === "settings") loadSettings();
}

// ----------------------------- Devices -------------------------------------
async function loadDevices() {
  currentDevices = await api("/api/devices");
  // Device list UI removed in favour of status cards (which carry Edit/Del).
  // We still keep currentDevices for selects and refresh dependent views.
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
    if (!statuses.length) { grid.innerHTML = "<p style='color:var(--muted)'>No devices yet. Add one from the sidebar.</p>"; return; }
    for (const s of statuses) {
      const card = document.createElement("div");
      card.className = "status-card";
      const cls = s.reachable ? "ok" : "down";
      const bar = (label, pct) => pct == null ? "" : `
        <div class="metric"><span>${label}</span><b>${pct.toFixed(0)}%</b></div>
        <div class="bar ${pct < 60 ? "ok" : pct < 85 ? "warn" : "bad"}"><span style="width:${pct}%"></span></div>`;
      card.innerHTML = `
        <div class="sc-title"><span>${escapeHtml(s.name)}</span><span class="dot ${cls}"></span></div>
        <div class="metric"><span>Host</span><b>${escapeHtml(s.host)}</b></div>
        ${s.reachable ? `
          ${bar("CPU load", s.cpu_load)}
          ${bar("Memory", s.mem_used_pct)}
          ${bar("Disk", s.disk_used_pct)}
          <div class="metric"><span>Uptime</span><b>${escapeHtml(s.uptime || "-")}</b></div>
        ` : `<div class="metric"><span>Status</span><b style="color:var(--danger)">unreachable</b></div>
             <div class="metric"><span></span><b>${escapeHtml(s.message)}</b></div>`}
        <div class="di-actions" style="margin-top:10px">
          <button class="btn btn-primary btn-icon-text" data-shell="${s.id}" title="Open shell">${icon("terminal")}<span>Shell</span></button>
          <button class="btn btn-ghost btn-icon" data-files="${s.id}" title="File manager">${icon("folder")}</button>
          <button class="btn btn-ghost btn-icon" data-edit="${s.id}" title="Edit device">${icon("edit")}</button>
          <button class="btn btn-danger btn-icon" data-del="${s.id}" title="Delete device">${icon("trash")}</button>
        </div>`;
      grid.appendChild(card);
      card.querySelector("[data-shell]").onclick = () => openTerminal(s.id, s.name);
      const fb = card.querySelector("[data-files]");
      if (fb) fb.onclick = () => { switchTab("files"); document.getElementById("files-device").value = s.id; loadFiles(); };
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
    api(`/api/devices/${id}`).then(d => { sel.value = d.bastion_id ? String(d.bastion_id) : ""; }).catch(() => {});
  }
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }

document.getElementById("add-device").onclick = () => openModal();
document.getElementById("modal-cancel").onclick = closeModal;

document.getElementById("f-auth").onchange = (e) => {
  const isKey = e.target.value === "key";
  document.getElementById("f-pass-label").classList.toggle("hidden", isKey);
  document.getElementById("f-key-label").classList.toggle("hidden", !isKey);
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
document.getElementById("inv-ansible").onclick = () => window.open("/api/devices/inventory/ansible", "_blank");
document.getElementById("inv-terraform").onclick = () => window.open("/api/devices/inventory/terraform", "_blank");
document.getElementById("import-devices").onclick = () => {
  document.getElementById("import-modal").classList.remove("hidden");
};
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
  for (const d of currentDevices) {
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
}
document.getElementById("bulk-run").onclick = async () => {
  const cmd = document.getElementById("bulk-command").value.trim();
  if (!cmd) { showToast("Enter a command", "error"); return; }
  const ids = [...document.querySelectorAll("#bulk-devices input:checked")].map(c => +c.value);
  if (!ids.length) { showToast("Select at least one device", "error"); return; }
  const res = await api("/api/bulk/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_ids: ids, command: cmd }) });
  const box = document.getElementById("bulk-results");
  box.innerHTML = res.map(r => `
    <div class="bulk-result ${r.reachable ? "ok" : "down"}">
      <div class="br-head"><b>${escapeHtml(r.name)}</b> <span class="muted">${escapeHtml(r.host)}</span> ${r.reachable ? "✅" : "❌"}</div>
      <pre class="br-out">${escapeHtml(r.reachable ? r.output : r.error)}</pre>
    </div>`).join("");
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
  for (const d of currentDevices) {
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
        <td>${escapeHtml(c.name)}</td>
        <td class="muted">${escapeHtml(c.image)}</td>
        <td>${escapeHtml(c.state)}</td>
        <td>${escapeHtml(c.status)}</td>
        <td class="muted">${escapeHtml(c.ports || '-')}</td>
        <td class="di-actions">
          <button class="btn btn-ghost btn-icon-xs" data-logs="${escapeHtml(c.id)}" title="Logs">${icon("logs")}</button>
          ${c.state === 'running'
            ? `<button class="btn btn-ghost btn-icon-xs" data-act="stop" data-cid="${escapeHtml(c.id)}" title="Stop">${icon("stop")}</button>
               <button class="btn btn-ghost btn-icon-xs" data-act="pause" data-cid="${escapeHtml(c.id)}" title="Pause">${icon("pause")}</button>
               <button class="btn btn-ghost btn-icon-xs danger" data-act="kill" data-cid="${escapeHtml(c.id)}" title="Kill">${icon("kill")}</button>`
            : `<button class="btn btn-ghost btn-icon-xs" data-act="start" data-cid="${escapeHtml(c.id)}" title="Start">${icon("play")}</button>
               <button class="btn btn-ghost btn-icon-xs" data-act="unpause" data-cid="${escapeHtml(c.id)}" title="Unpause">${icon("play")}</button>`}
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
        <td>${u.id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td><select class="input-sm user-role" data-id="${u.id}">
          ${["viewer", "operator", "admin"].map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
        </select></td>
        <td class="muted">${new Date(u.created_at).toLocaleDateString()}</td>
        <td class="di-actions">
          <button class="btn btn-ghost btn-icon-xs danger user-del" data-id="${u.id}" data-name="${escapeHtml(u.username)}" title="Delete user">${icon("trash")}</button>
        </td>
      </tr>`).join("")}</tbody></table>`;
    box.querySelectorAll(".user-role").forEach(s => s.onchange = async () => {
      await api(`/api/users/${s.dataset.id}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: s.value }) });
      loadUsers();
    });
    box.querySelectorAll(".user-del").forEach(b => b.onclick = async () => {
      if (!confirm(`Delete user ${b.dataset.name}?`)) return;
      await api(`/api/users/${b.dataset.id}`, { method: "DELETE" });
      loadUsers();
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
        <div class="sc-name">${escapeHtml(t.name)} ${t.enabled ? "" : "<span class='muted'>(paused)</span>"}</div>
        <pre class="sc-cmd">${escapeHtml(t.command)}</pre>
        <div class="muted" style="font-size:12px">Devices: ${t.device_ids.join(", ") || "-"} · every ${t.interval_minutes}m · next: ${t.next_run ? new Date(t.next_run).toLocaleString() : "-"}</div>
        <div class="di-actions">
          <button class="btn btn-danger btn-icon" data-del-task="${t.id}" title="Delete task">${icon("trash")}</button>
        </div>
      </div>`).join("");
    box.querySelectorAll("[data-del-task]").forEach(b => b.onclick = async () => {
      if (!confirm("Delete this task?")) return;
      await api(`/api/scheduled/${b.dataset.delTask}`, { method: "DELETE" });
      loadScheduled();
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
document.getElementById("sched-cancel").onclick = () => document.getElementById("sched-form").classList.add("hidden");
document.getElementById("sched-save").onclick = async () => {
  const name = document.getElementById("sched-name").value.trim();
  const command = document.getElementById("sched-command").value.trim();
  const ids = Array.from(document.querySelectorAll("#sched-devices input[type=checkbox]:checked")).map(c => parseInt(c.value, 10));
  const interval = parseInt(document.getElementById("sched-interval").value, 10) || 60;
  if (!name || !command) { showToast("Name & command required", "error"); return; }
  try {
    await api("/api/scheduled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, command, device_ids: ids, interval_minutes: interval }) });
    document.getElementById("sched-form").classList.add("hidden");
    loadScheduled();
    showToast("Task created", "ok");
  } catch (e) { showToast(e.message, "error"); }
};

// ----------------------------- Settings (notifications) ---------------------
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    document.getElementById("set-notify").checked = s.notify_enabled;
    document.getElementById("set-tg-chat").value = s.telegram_chat_id || "";
    document.getElementById("set-discord").value = s.discord_webhook || "";
    document.getElementById("set-interval").value = s.monitor_interval;
    document.getElementById("set-public").checked = s.public_dashboard;
    document.getElementById("set-tg-token").value = "";  // never echo token back
  } catch (e) { showToast(e.message, "error"); }
}
document.getElementById("set-save").onclick = async () => {
  const payload = {
  notify_enabled: document.getElementById("set-notify").checked,
  telegram_chat_id: document.getElementById("set-tg-chat").value.trim(),
  discord_webhook: document.getElementById("set-discord").value.trim(),
  monitor_interval: parseInt(document.getElementById("set-interval").value, 10) || 60,
  public_dashboard: document.getElementById("set-public").checked,
  };
  const tok = document.getElementById("set-tg-token").value.trim();
  if (tok) payload.telegram_token = tok;
  try {
    await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    showToast("Settings saved", "ok");
  } catch (e) { showToast(e.message, "error"); }
};
document.getElementById("set-test").onclick = async () => {
  const r = await api("/api/settings/test", { method: "POST" });
  showToast("Test sent: " + JSON.stringify(r), "ok");
};

// ----------------------------- Boot ----------------------------------------
document.querySelectorAll(".side-nav-item").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
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
  // Register service worker for PWA installability.
  if ("serviceWorker" in navigator) {
    try { await navigator.serviceWorker.register("/static/sw.js"); } catch (_) {}
  }
})();
