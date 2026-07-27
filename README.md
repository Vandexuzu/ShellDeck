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
| 📁 **SFTP manager** | Browse / upload / download / edit / delete remote files; filter; drag-and-drop; upload-from-URL |
| ⚡ **Bulk runner** | Run one command on many devices; bulk-edit or bulk-delete |
| 📝 **Snippets** | Save reusable commands, run on any device |
| 🐳 **Docker** | Start / stop / restart / logs / exec into containers |
| ⏰ **Scheduler** | Recurring or run-once jobs per device |
| 🔔 **Alerts** | Telegram · Discord · ntfy · Gotify · Slack · Email · webhook |
| 🕸 **Agent relay** | Reach devices behind NAT/firewall — full shell **and** file manager over an outbound WebSocket |
| 🌐 **Public dashboard** | Read-only status page at `/public` (no login) |
| 🪜 **Bastion** | Tunnel SSH through a jump host automatically |
| 📲 **PWA** | Install to phone home screen; mobile UI (cards + bottom nav) |

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
- **Frontend:** vanilla JS · xterm.js · hand-written CSS (no build step, offline-friendly, inline SVG icons — no CDN)

---

## ⚙️ Configuration (`.env`)

| Key | Default | Notes |
|-----|---------|-------|
| `SECRET_KEY` | `change-me` | JWT + credential encryption. **Change in prod.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime |
| `DATABASE_URL` | `sqlite:///./shelldeck.db` | DB connection |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind (Docker) |
| `SSH_IGNORE_KNOWN_HOSTS` | `true` | Homelab-friendly; set `false` for stricter security |

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

- [x] Cron-expression schedules
- [x] WebSocket agents (reverse tunnel for NAT devices)
- [x] Multi-tab terminal with device picker
- [x] Split terminal panes
- [x] Tab persistence across reload
- [x] File manager over agent relay (NAT devices)
- [ ] Session recording playback (asciinema-style)
- [ ] 2FA / OIDC
- [ ] Prometheus metrics export

## 🤝 Contributing

Keep it **$0, agentless, self-hosted**. Run `pytest` before opening a pull request on ShellDeck.

## 👤 Author

Built by [@vandikampw](https://instagram.com/vandikampw) · ⚡ ShellDeck

## 📄 License

[MIT](LICENSE)
