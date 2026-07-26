# ⚡ ShellDeck

**Self-hosted web control panel to manage, monitor, and remotely shell into your hardware — from one browser UI.**

Add your servers, VMs, and IoT devices, see live health (CPU / memory / disk / uptime), open a full interactive **SSH shell in the browser** via [xterm.js](https://xtermjs.org/), and run commands across many hosts at once. Transfer files over SFTP, manage Docker containers, schedule recurring jobs, tag and bulk-edit devices, and get alerted on Telegram / Discord / ntfy / Gotify / Slack / Email / any webhook when a device goes down — all from a single, installable PWA.

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
  - **Tags** — label devices (`prod`, `web`, `critical`, …) and filter the device list by tag.
  - **Tailscale** — mark a device as reachable over Tailscale and use the **Discover** button to import nodes from your Tailscale network in one click.
- 📊 **Live monitoring** — poll CPU load, memory %, disk %, uptime over SSH. Status cards turn red when a host is unreachable.
- 💻 **In-browser shell** — real interactive terminal (xterm.js ⇄ asyncssh over WebSocket). Full PTY, colors, resize support.
- 📁 **SFTP file manager** — browse, upload, download, edit, rename, and delete remote files through the UI.
- ⚡ **Bulk command runner** — execute one command across many selected devices at once. Also supports **bulk edit** (tags / OS / notes / bastion) and **bulk delete**.
- 📝 **Saved snippets** — store reusable command snippets and run them on any device with one click. Import/export supported.
- 🐳 **Docker manager** — list containers on a host, plus start / stop / restart / pause / kill / remove, stream **logs**, view **stats**, run free-form `docker` commands, and open an **interactive `exec`** shell into a container.
- 📤 **Export / Import** — back up your device list as JSON (encrypted credentials stay encrypted) and restore it elsewhere.
- 🗂 **Inventory export** — one-click export of devices as an **Ansible inventory** (`shelldeck-inventory.ini`) or **Terraform inventory** (`shelldeck-inventory.tf`) for use with your existing DevOps tooling.
- 🌐 **Public health dashboard** — flip a switch in Settings to publish a read-only status page at `/public` (no login required) showing each device's reachability, CPU/memory/disk, and uptime.
- 🪜 **Jump host / bastion** — mark any device as a bastion for another; ShellDeck tunnels the SSH connection through it automatically (great for NAT-traversed or segmented networks).
- 🔔 **Multi-channel alerts (Settings → Notifications)** — background monitor pings each device every N seconds; on a reachability change it sends a message to **one or more** of: **Telegram**, **Discord**, **ntfy**, **Gotify**, **Slack**, **Email (SMTP)**, or any **custom webhook** (POST JSON). "Send test" button included.
- ⏰ **Scheduled tasks** — create recurring jobs (command + target devices + interval in minutes). Also supports **run-once** tasks, optionally scheduled at a specific time (`run_at`), and a **Run now** trigger. Import/export supported.
- 📜 **Session history & command log** — every shell session is recorded (who, when, duration). Open a session to view the **commands** that were typed during it.
- 📲 **PWA / Installable** — add ShellDeck to your phone's home screen (Brave/Chrome) for a native-app feel, with offline shell caching. Mobile UI hides nav labels and renders tables as cards for small screens.
- 🛡 **Audit trail** — every shell session is logged (who, when, transcript + per-command history).
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
pytest          # 13 tests: auth, RBAC, devices, tags, bulk, tailscale, settings, notifications, scheduled tasks, sessions
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

1. Enable alerts and set the **monitor interval** (seconds).
2. Configure **any** combination of channels:
   - **Telegram** — bot token (from [@BotFather](https://t.me/BotFather), encrypted at rest) + chat id (from [@userinfobot](https://t.me/userinfobot) or [@getidsbot](https://t.me/getidsbot)).
   - **Discord** — incoming webhook URL.
   - **ntfy** — topic URL (e.g. `https://ntfy.sh/your-topic`).
   - **Gotify** — message URL with token.
   - **Slack** — incoming webhook URL.
   - **Email** — SMTP host / port / user / password + recipient.
   - **Custom webhook** — any URL that accepts a POST with JSON `{"event": …, "device": …, "status": …}`.
3. Click **Send test** to verify each configured channel.

> Telegram bot tokens and SMTP passwords are encrypted at rest and never echoed back to the browser.

### Tailscale devices

If ShellDeck runs on a machine that is part of your Tailscale network, click the **TS (Discover)** button on the Devices page to scan `tailscale status --json` and import any not-yet-added nodes in one click. Discovered devices are auto-flagged as Tailscale-reachable. (Requires the `tailscale` CLI on the host.)

### Tags & bulk operations

- Add **tags** to a device (comma-separated) to group them. Filter the device list with the **tag dropdown**.
- In the **Bulk** view, select multiple devices to run a command, **bulk-edit** shared fields (tags / OS / notes / bastion), or **bulk-delete**.

### Session command history

Open **Sessions** to see every shell session. Click the list icon on a row to view the **commands** typed during that session (audit trail).

## 👥 Roles & permissions

| Capability | admin | operator | viewer |
|------------|:-----:|:--------:|:------:|
| View devices / monitoring | ✅ | ✅ | ✅ |
| Open shell / SFTP / run commands | ✅ | ✅ | ❌ |
| Manage devices, snippets, Docker, scheduled tasks | ✅ | ✅ | ❌ |
| Bulk edit / delete devices | ✅ | ✅ | ❌ |
| Manage users & roles | ✅ | ❌ | ❌ |
| Configure alerts (Settings) | ✅ | ✅* | ❌ |

\* Settings is available to operators too (alerts are global, not per-user).

## 🗺 Roadmap

- [ ] Session **recording playback** (asciinema-style)
- [ ] WebSocket-based lightweight **agent** for NAT-traversed devices
- [ ] 2FA / OIDC
- [ ] Per-device connection pooling / concurrency limits
- [ ] Cron-expression schedules
- [ ] Prometheus metrics export

## 🤝 Contributing

ShellDeck welcome. Keep it $0, agentless, and self-hosted. Run `pytest` before opening a ShellDeck.

## 📄 License

[MIT](LICENSE)
