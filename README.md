# ⚡ ShellDeck

**Self-hosted web panel to manage, monitor & SSH into your servers — from any browser.**

Open a real terminal in the browser, run commands across hosts at once, transfer files over SFTP, manage Docker, schedule jobs, and get alerts when a device goes down. **$0, agentless, one Docker Compose file.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Self-hosted](https://img.shields.io/badge/self--hosted-✓-green.svg)](https://github.com/Vandexuzu/ShellDeck)
[![PWA](https://img.shields.io/badge/PWA-installable-purple.svg)](https://github.com/Vandexuzu/ShellDeck)
[![CI](https://github.com/Vandexuzu/ShellDeck/actions/workflows/ci.yml/badge.svg)](https://github.com/Vandexuzu/ShellDeck/actions/workflows/ci.yml)

> 💸 **No cloud. No agents. No per-seat pricing.** One Compose file + a SQLite file and you're done.
> 🔒 Credentials encrypted at rest (Fernet). Every shell session is audit-logged.

---

## ✨ What it does

| | |
|---|---|
| 🖥 **Devices** | Add hosts (password / SSH-key), tag them, filter by tag, Tailscale auto-discover |
| 📊 **Live monitor** | CPU / memory / disk / uptime over SSH; cards go red when down |
| 💻 **In-browser shell** | Real PTY terminal (xterm.js ⇄ asyncssh over WebSocket); multi-tab, split panes, survives reload |
| 🔍 **Terminal search** | In-terminal find via `Ctrl/Cmd+F` (xterm SearchAddon) |
| 🔁 **Command re-run** | Replay any recorded session's commands on its device straight from Session History |
| ⌨️ **Command palette** | `Ctrl/Cmd+K` quick-navigate to any view, device terminal, or action |
| 📁 **SFTP manager** | Browse / upload (with live **progress bar**) / download / edit / delete; drag-and-drop; upload-from-URL |
| ⚡ **Bulk runner** | Run one command on many devices; bulk-edit or bulk-delete |
| 📝 **Snippets** | Save reusable commands, **organise by category**, run on one device or bulk to all |
| 🐳 **Docker** | Start / stop / restart / logs / exec into containers |
| ⏰ **Scheduler** | Recurring or run-once jobs per device |
| 🔔 **Alerts** | Telegram · Discord · ntfy · Gotify · Slack · Email · webhook |
| 🕸 **Agent relay** | Reach devices behind NAT/firewall — full shell **and** file manager over an outbound WebSocket |
| 🌐 **Public dashboard** | Read-only status page at `/public` (no login) — host IPs are masked (e.g. `10.0.0.x`) |
| 🪜 **Bastion** | Tunnel SSH through a jump host automatically |
| 📲 **PWA** | Install to phone home screen; mobile UI (cards + bottom nav) |
| 🔐 **2FA** | TOTP / 2FA (RFC 6238, no external dependency); OIDC SSO (Google / GitHub / corporate) |

---

## 👥 Roles

| Capability | 🛡 admin | 🔧 operator | 👁 viewer |
|---|:---:|:---:|:---:|
| View all devices / monitoring | ✅ | ✅ (own + admin fleet) | ✅ |
| Shell / SFTP / Docker / run | ✅ | ✅ **own devices only** | ❌ |
| Manage devices / snippets / scheduled | ✅ | ✅ **own devices only** | ❌ |
| Bulk edit / delete | ✅ | ✅ **own devices only** | ❌ |
| Manage users & settings | ✅ | ❌ | ❌ |

- **admin** — full control of everything; first registered user becomes admin.
- **operator** — full control of their *own* devices; read-only view of the admin's shared fleet.
- **viewer** — read-only view of *all* devices; no shell, no writes.

---

## 🚀 Quick start

```bash
cp .env.example .env        # set a STRONG SECRET_KEY
docker compose up -d --build
# open http://localhost:8000  →  register the first account (admin)
```

### Behind a reverse proxy (recommended)

ShellDeck serves plain HTTP — put it behind Nginx/Caddy/Traefik with TLS:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;     # WS terminal
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;                    # keep shells alive
}
```

---

## 🔒 Security

ShellDeck is built for self-hosting, but treat it like the keys to your fleet:

- **Credentials encrypted at rest** (Fernet, key derived from `SECRET_KEY`). Every device password / SSH key is stored encrypted.
- **Password hashing** uses PBKDF2-HMAC-SHA256 (200k iterations) with a per-user salt; token comparison is constant-time.
- **Two-factor auth (TOTP)** — RFC 6238, no external service. The 2FA code is submitted in a dedicated `totp` form field (not OAuth2 `scope`).
- **Login rate-limiting** — failed logins are throttled per client IP (10 failures / 15 min → temporary 429 lockout).
- **OIDC auto-provisioning is OFF by default** — SSO users must already have a ShellDeck account; unknown IdP identities are rejected (no silent account creation).
- **Public dashboard IP masking** — host addresses on `/public` are anonymised (`10.0.0.42` → `10.0.0.x`).
- **SSRF protection** — the *upload-from-URL* feature rejects URLs that resolve to private / loopback / link-local addresses.
- **Session audit logging** — every shell session is recorded (and, when enabled, replayable as a TTY cast).

> ⚠️ **Front it with TLS.** ShellDeck serves plain HTTP; expose it through Nginx/Caddy/Traefik with HTTPS so credentials and 2FA codes aren't sent in clear text.

---

## 🧱 Architecture

```text
Browser (xterm.js + vanilla JS, PWA)
   │  HTTPS / WebSocket
   ▼
FastAPI ── asyncssh ──► your devices (agentless)
   ├─ /api/terminal/{id}   interactive shell
   ├─ /api/bulk            parallel SSH
   ├─ /api/docker          remote docker CLI
   ├─ /api/files/{id}      SFTP browse/upload/download
   ├─ /api/agents/ws       outbound tunnel from NAT devices
   └─ background loops: monitor (alerts) + scheduler (jobs)
   ▼
SQLite  (users · devices · snippets · scheduled_tasks · session_logs · settings · agents)
```

- **Backend:** FastAPI · asyncssh · SQLAlchemy · SQLite
- **Frontend:** vanilla JS · xterm.js (via CDN) · hand-written CSS (no build step, offline-friendly, inline SVG icons)

---

## ⚙️ Configuration (`.env`)

| Key | Default | Notes |
|-----|---------|-------|
| `SECRET_KEY` | `change-me` | JWT + credential encryption. **Change in prod.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime |
| `DATABASE_URL` | `sqlite:///./shelldeck.db` | DB connection |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind (Docker) |
| `SSH_IGNORE_KNOWN_HOSTS` | `true` | Homelab-friendly; set `false` for stricter security |
| `OIDC_AUTO_PROVISION` | `false` | When `true`, first SSO login auto-creates a viewer account. Leave `false` so only pre-existing users can sign in via OIDC. |

> **Brute-force protection:** failed logins are throttled per client IP — 10 failures within 15 minutes triggers a temporary lockout (HTTP 429). Tune in `app/main.py` (`_LOGIN_WINDOW` / `_LOGIN_MAX_FAILS`).

### Alerts (Settings → Notifications)
Enable alerts, set the monitor interval, then wire up any channel — **Telegram** (bot token + chat id), **Discord**, **ntfy**, **Gotify**, **Slack**, **Email (SMTP)**, or a **custom webhook**. Hit **Send test** to verify.

---

## 🛠 Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## ✅ Tests

```bash
pytest        # auth · RBAC · devices · tags · bulk · docker · settings · alerts · scheduled
```

---

## 🗺 Roadmap

**Shipped recently**
- [x] Terminal in-terminal search (`Ctrl/Cmd+F`)
- [x] Session command re-run (replay recorded commands on device)
- [x] Command palette (`Ctrl/Cmd+K`) for quick navigation
- [x] Upload progress bar (SFTP)
- [x] Snippet categories + filter
- [x] Starter snippet templates seeded on first install
- [x] App identity (version, author, GitHub link in UI)

**Earlier milestones**
- [x] Cron-expression schedules
- [x] WebSocket agents (reverse tunnel for NAT devices)
- [x] Multi-tab terminal with device picker
- [x] Split terminal panes · tab persistence across reload
- [x] Session recording playback (asciinema-style TTY replay)
- [x] Terminal broadcast
- [x] Audit search (filter session history by device/host/command)
- [x] Two-factor auth (TOTP) + OIDC SSO
- [x] Home dashboard (stat cards, device health, recent activity)

**Planned**
- [ ] Resource history graphs (CPU/mem over time)
- [ ] Per-device public share links
- [ ] API bot tokens (non-user)
- [ ] Password reset flow
- [ ] Mobile terminal touch optimisations

See the [issue tracker](https://github.com/Vandexuzu/ShellDeck/issues) for more.

## 🤝 Contributing

Keep it **$0, agentless, self-hosted**. Run `pytest` before opening a pull request on ShellDeck.

## 👤 Author

Built by **Vandexuzu** ·  ShellDeck — [github.com/Vandexuzu/ShellDeck](https://github.com/Vandexuzu/ShellDeck)

## 📄 License

[MIT](LICENSE)
