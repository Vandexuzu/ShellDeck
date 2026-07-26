# ⚡ ShellDeck

**Self-hosted web control panel to manage, monitor, and remotely shell into your hardware — from one browser UI.**

Add your servers, VMs, and IoT devices, see live health (CPU / memory / disk / uptime), open a full interactive **SSH shell in the browser** via [xterm.js](https://xtermjs.org/), and run commands across many hosts at once. Transfer files over SFTP, manage Docker containers, schedule recurring jobs, and get alerted on Telegram/Discord when a device goes down — all from a single, installable PWA.

> 💸 **$0 / fully self-hosted.** No cloud, no agents, no per-seat pricing. One Docker Compose file, a SQLite file, and you're done.

> ⚠️ **Security notice:** ShellDeck can execute commands on your machines. Always run it behind a trusted network / reverse proxy with HTTPS, use a strong `SECRET_KEY`, and restrict who gets an account. Credentials are encrypted at rest (Fernet); every shell session is recorded in an audit log.

---

## ✨ Features

- 🔐 **Authentication & RBAC** — register/login with JWT sessions. Three roles:
  - **admin** — full access + user management
  - **operator** — manage devices, shells, files, Docker, snippets, scheduled tasks (but cannot manage users)
  - **viewer** — read-only (no shell, no writes)
  - First registered user becomes admin; new registrations default to least-privilege **viewer**.
- 🖥 **Device management** — add / edit / remove hosts with **password** or **SSH-key** auth. Credentials are encrypted at rest.
- 📊 **Live monitoring** — poll CPU load, memory %, disk %, uptime over SSH. Status cards turn red when a host is unreachable.
- 💻 **In-browser shell** — real interactive terminal (xterm.js ⇄ asyncssh over WebSocket). Full PTY, colors, resize support.
- 📁 **SFTP file manager** — browse, upload, download, edit, rename, and delete remote files through the UI.
- ⚡ **Bulk command runner** — execute one command across many selected devices at once.
- 📝 **Saved snippets** — store reusable command snippets and run them on any device with one click.
- 🐳 **Docker manager** — list containers on a host, plus start / stop / restart / pause / kill / remove, stream **logs**, view **stats**, run free-form `docker` commands, and open an **interactive `exec`** shell into a container.
- 📤 **Export / Import** — back up your device list as JSON (encrypted credentials stay encrypted) and restore it elsewhere.
- 🔔 **Alerts (Settings → Notifications)** — background monitor pings each device every N seconds; on a reachability change it sends a message to **Telegram** (bot token, encrypted at rest) and/or a **Discord webhook**. "Send test" button included.
- ⏰ **Scheduled tasks** — create recurring jobs (command + target devices + interval in minutes). A background scheduler runs due tasks automatically — great for updates, health checks, or cleanup.
- 📲 **PWA / Installable** — add ShellDeck to your phone's home screen (Brave/Chrome) for a native-app feel, with offline shell caching.
- 🛡 **Audit trail** — every shell session is logged (who, when, transcript).
- 🐳 **$0 deploy** — Docker Compose, SQLite, no external services.

## 🧱 Architecture

```
Browser (xterm.js + vanilla JS, installable PWA)
   │  HTTPS / WS
   ▼
FastAPI  ── SSH (asyncssh) ──►  your devices
   │  ├─ /api/terminal/{id}   (WebSocket shell)
   │  ├─ /api/bulk            (parallel SSH)
   │  ├─ /api/docker          (remote `docker` CLI)
   │  └─ background loops: monitor (alerts) + scheduler (jobs)
   ▼
SQLite  (users, devices, snippets, scheduled_tasks, session_logs, settings)
```

- **Backend:** FastAPI + asyncssh + SQLAlchemy + SQLite
- **Frontend:** vanilla JS + xterm.js + hand-written CSS (no build step, offline-friendly, Lucide inline SVG icons — no CDN)
- **Realtime shell:** `WebSocket /api/terminal/{device_id}` bridges xterm.js to an SSH session.
- **Background workers:** asyncio tasks started in the app lifespan — reachability monitor (alerts) and job scheduler.

## 🚀 Quick start (Docker — recommended)

```bash
cp .env.example .env
# edit .env and set a STRONG SECRET_KEY
docker compose up -d --build
# open http://localhost:8000
```

The first account you register becomes **admin**.

### Behind a reverse proxy (recommended)

ShellDeck ships plain HTTP. Put it behind Nginx/Caddy/Traefik with TLS:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;   # required for the WS terminal
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;                  # keep long-lived shells open
}
```

> The WebSocket terminal needs `Upgrade`/`Connection` headers and a generous read timeout.

## 🛠 Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000 and register your first user.

## ✅ Tests

```bash
pip install -r requirements.txt
pytest          # 8 tests: auth, RBAC, devices, settings, scheduled tasks
```

## 🔧 Configuration (`.env`)

| Key | Default | Description |
|-----|---------|-------------|
| `SECRET_KEY` | `change-me...` | JWT signing + credential encryption key. **Change in prod.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime (minutes). |
| `DATABASE_URL` | `sqlite:///./shelldeck.db` | DB connection string. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address (used by Docker). |
| `SSH_IGNORE_KNOWN_HOSTS` | `true` | Skip SSH host-key checks (homelab-friendly, weaker MITM protection). Set `false` for stricter security. |

### Alerts setup (Settings → Notifications)

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather) → copy the token.
2. Get your chat id (message [@userinfobot](https://t.me/userinfobot), or forward a message to [@getidsbot](https://t.me/getidsbot)).
3. In ShellDeck **Settings**, paste the token + chat id, enable alerts, set the monitor interval, and click **Send test**.
4. *(Optional)* paste a **Discord webhook** URL to also receive alerts there.

> Telegram bot tokens are encrypted at rest. The token is never echoed back to the browser.

## 👥 Roles & permissions

| Capability | admin | operator | viewer |
|------------|:-----:|:--------:|:------:|
| View devices / monitoring | ✅ | ✅ | ✅ |
| Open shell / SFTP / run commands | ✅ | ✅ | ❌ |
| Manage devices, snippets, Docker, scheduled tasks | ✅ | ✅ | ❌ |
| Manage users & roles | ✅ | ❌ | ❌ |
| Configure alerts (Settings) | ✅ | ✅* | ❌ |

\* Settings is available to operators too (alerts are global, not per-user).

## 🗺 Roadmap

- [ ] WebSocket-based lightweight **agent** for NAT-traversed devices
- [ ] Session **recording playback** (asciinema-style)
- [ ] Jump host / bastion support
- [ ] Ansible / Terraform inventory export
- [ ] Public read-only health dashboard
- [ ] 2FA / OIDC

## 🤝 Contributing

PRs welcome. Keep it $0, agentless, and self-hosted. Run `pytest` before opening a PR.

## 📄 License

[MIT](LICENSE)
