# ShellDeck — Full Documentation

This document complements `README.md` with in-depth technical detail: architecture,
installation, configuration, API reference, data model, RBAC, and troubleshooting.

---

## 1. Overview

**ShellDeck** is a self-hosted web panel for managing, monitoring, and SSH-ing into
your servers from any browser. One Docker Compose file + one SQLite file — no cloud,
no mandatory agent on the target devices (agentless by default; optional agents for
NAT/firewall hosts), no cost.

| Item | Value |
|------|-------|
| Repo | https://github.com/Vandexuzu/ShellDeck |
| License | MIT |
| Backend | Python 3.11+ (FastAPI) |
| Frontend | Vanilla JS + xterm.js (PWA, no build step) |
| DB | SQLite (`data/shelldeck.db`) |
| Author | Vandexuzu |

Philosophy: **$0, self-hosted.** ShellDeck connects to devices over
regular SSH (asyncssh) by default — target devices need to install nothing. For hosts
behind NAT/firewall, an **optional agent** (`agent/client.py`) opens an outbound
WebSocket tunnel so ShellDeck can still reach it (shell, files, monitoring).

---

## 2. Architecture

```
Browser (PWA: xterm.js + vanilla JS)
   │  HTTP + WebSocket
   ▼
ShellDeck (FastAPI, container, network_mode: host)
   ├─ Routers (app/routers/*.py)
   │    auth · devices · monitoring · terminal · snippets · bulk
   │    files · docker · users · settings · scheduled · public
   │    agents · backup · oidc · home
   ├─ Core: security (JWT) · config (Fernet) · notifications · cron
   └─ Background loops: monitor (alerts) + scheduler (cron jobs)
   ▼
SQLite  (users · devices · snippets · scheduled_tasks · session_logs
         settings · agents · audit_log · topology_snapshots)
   ▼ (SSH, outbound)
Target device (password / SSH-key / bastion / Tailscale / agent-tunnel)
```

**Key points:**
- **Terminal** runs over a WebSocket — `/api/terminal/{id}` (direct SSH) or
  `/api/agents/terminal/{id}` (through an agent tunnel). Both bridge xterm.js ⇄ a
  remote PTY. Multi-tab, split panes, survives reload.
- **Monitor** runs as a background loop: every `monitor_interval` seconds it checks
  CPU/mem/disk/uptime via SSH (or, for agent-only devices, through the agent tunnel);
  if a device is down it fires an alert.
- **Scheduler** runs `ScheduledTask` entries (interval or cron) via parallel SSH.
- **Agent relay**: a device behind NAT dials *out* over a WebSocket (`/api/agents/ws`);
  ShellDeck relays the shell (`/api/agents/terminal/{id}`), file manager
  (`/api/agents/fs/{id}`), and monitoring through that live tunnel — no inbound port
  needed. The agent auto-reports its OS so metrics use the right command set
  (PowerShell on Windows, `cat/free/df` on Linux).

---

## 3. Installation

### 3.1 Docker (recommended)
```bash
cp .env.example .env        # required: set a strong SECRET_KEY
docker compose up -d --build
# open http://localhost:8000 → register the first account (auto admin)
```

`docker-compose.yml` uses `network_mode: host` so the container can reach the LAN for
the Network Topology / scan features. To use bridge mode instead, comment the
`network_mode: host` line and uncomment the `ports` block.

Persistent data lives in the `./data` volume (DB + `.env`).

### 3.2 Local dev
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

### 3.3 Reverse proxy (required for production)
ShellDeck serves plain HTTP — put it behind Nginx/Caddy/Traefik with TLS:
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;      # WS terminal
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;                     # keep shells alive
}
```

---

## 4. Configuration (`.env`)

| Key | Default | Notes |
|-----|---------|-------|
| `SECRET_KEY` | `change-me-to-a-long-random-string` | JWT + credential encryption (Fernet). **Change in prod.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime (minutes) |
| `DATABASE_URL` | `sqlite:///./shelldeck.db` | DB connection |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind inside container |
| `SSH_IGNORE_KNOWN_HOSTS` | `true` | Homelab-friendly; `false` for stricter security |
| `OIDC_ENABLED` | `false` | Enable OIDC SSO |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | – | IdP credentials |
| `OIDC_DISCOVERY_URL` | – | e.g. `https://accounts.google.com/.well-known/openid-configuration` |
| `OIDC_AUTO_PROVISION` | `false` | If `true`, first SSO login auto-creates a viewer. Default `false` = only pre-existing users may sign in via OIDC. |

**Brute-force protection:** failed logins are throttled per client IP — 10 failures
within 15 minutes → temporary lockout (HTTP 429). Tunable in `app/main.py`
(`_LOGIN_WINDOW` / `_LOGIN_MAX_FAILS`).

---

## 5. Data Model (SQLAlchemy)

| Table | Main contents |
|-------|---------------|
| `users` | username, password_hash (pbkdf2), role, is_admin, totp_secret |
| `devices` | owner_id, name, host, port, username, auth_method, password_enc (Fernet), private_key_enc (Fernet), bastion_id, tags, tailscale, os, notes, last_seen |
| `session_logs` | device_id, user_id, started_at, ended_at, transcript, commands, recording (asciinema JSON) |
| `snippets` | owner_id, name, command, category |
| `settings` | singleton (id=1): notifications (telegram/discord/ntfy/gotify/slack/email/webhook), monitor_interval, public_dashboard, oidc_enabled, theme, session_retention_days (0 = keep forever), agent_heartbeat (s), agent_reconnect (s) |
| `scheduled_tasks` | owner_id, command, device_ids (JSON), interval_minutes, cron, enabled, run_once, run_at, last_run, next_run, last_output |
| `agents` | owner_id, name, token (shared secret), device_id (linked device), connected, last_seen, ips (JSON list of reported interface IPs), os (reported OS: windows/linux/darwin) — for NAT relay |
| `audit_log` | user_id, username, action (login/login_failed/logout/…), detail, ip, created_at |
| `topology_snapshots` | scan_time, nodes_json, edges_json, discovered_json |

**Time:** all timestamps are stored as **UTC** (`datetime.now(timezone.utc)`).
The frontend renders them in the device's local TZ (WIB on Vandex's device) via the
`_asUTC()` helper — see note below.

---

## 6. Role-Based Access Control (RBAC)

| Capability | admin | operator | viewer |
|------------|:-----:|:--------:|:------:|
| View all devices / monitoring | ✅ | ✅ (own + admin fleet) | ✅ |
| Shell / SFTP / Docker / run | ✅ | ✅ **own devices only** | ❌ |
| Manage devices / snippets / scheduled | ✅ | ✅ **own devices only** | ❌ |
| Bulk edit / delete | ✅ | ✅ **own devices only** | ❌ |
| Manage users & settings | ✅ | ❌ | ❌ |

- **admin** — full control; the first registered user becomes admin automatically.
- **operator** — full control of their **own** devices; read-only view of the admin fleet.
- **viewer** — read-only view of all devices; no shell, no writes.

Main guards: `operator_only` (decorator) + `canAccessDevice()` (ownership check).

---

## 7. Features & Modules

### 7.1 Devices
Add hosts (password / SSH-key), tag them, filter by tag, bastion (jump host), Tailscale
auto-discover (`.ts.net`). **No device limit** — only server resources bound it.

### 7.2 Live Monitor
Checks CPU/mem/disk/uptime. For **direct-SSH** devices it uses asyncssh; for
**agent-only** devices it collects the same metrics *through the agent tunnel*. OS is
auto-detected: Linux/macOS use `uptime`/`cat /proc/loadavg`/`free`/`df`, **Windows** uses
PowerShell (Win32_Processor load %, FreePhysicalMemory, Get-PSDrive C, LastBootUpTime) —
uptime is formatted uniformly (`up 3 days, 8 hours, 51 minutes`). Cards turn red when a
device is down. Interval is set in Settings.

### 7.3 In-browser Shell
Real PTY (xterm.js ⇄ asyncssh over WebSocket). Multi-tab, split panes, survives reload.
Features: in-terminal search (`Ctrl/Cmd+F`), command palette (`Ctrl/Cmd+K`), broadcast.

### 7.4 Session History
Every session is recorded (transcript + commands + TTY recording asciinema). From
history you can **re-run** commands on the device, or **playback** (asciinema-style).

### 7.5 SFTP Manager
Browse / upload (progress bar) / download / edit / delete; drag-and-drop; upload-from-URL
(with SSRF protection — rejects private/loopback/link-local addresses).

### 7.6 Bulk Runner
Run one command across many devices; bulk-edit / bulk-delete.

### 7.7 Snippets
Save reusable commands, organise by category, run on one device or bulk.

### 7.8 Docker
Start / stop / restart / logs / exec into containers.

### 7.9 Scheduler
Recurring (interval) or cron (5-field) jobs per device. Last output is stored.

### 7.10 Alerts
Telegram · Discord · ntfy · Gotify · Slack · Email (SMTP) · custom webhook.
Enable in Settings → Notifications, set the interval, then "Send test".

### 7.11 Agent Relay (NAT)

A device behind a firewall dials out (`agent/client.py` → `/api/agents/ws`). ShellDeck
relays the shell **and** file manager **and** monitoring through that tunnel — no
inbound port on the device.

**Add an agent:** Agents → Add Agent → copy a **bootstrap helper** (Linux/macOS/Termux
one-liner, Windows PowerShell one-liner, or standalone `run_agent.sh` / `run_agent.ps1`).
Each helper injects the server URL, the agent token, and the **heartbeat / reconnect**
values from Global Settings, so the device connects with zero manual editing:

```powershell
# Windows example (bootstrap PowerShell one-liner)
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Vandexuzu/ShellDeck/main/agent/client.py' -OutFile shelldeck_agent.py
pip install websocket-client
$env:SHELLDECK_URL='https://shelldeck.example.com'; $env:SHELLDECK_AGENT_TOKEN='<token>'; $env:SHELLDECK_HEARTBEAT='15'; $env:SHELLDECK_RECONNECT='5'
python shelldeck_agent.py
```

**Agent IP discovery:** on connect the agent reports its local interface IPs. On the
Agents tab each IP has a **+ Add device** button that pre-fills a new device and
**auto-links it to that agent's tunnel** — so Shell / Files / Monitoring go through the
agent, not direct SSH.

**OS auto-detection:** the agent reports its OS (`windows` / `linux` / `darwin`). Devices
reached only via an agent are monitored *through* the tunnel; Windows devices use
PowerShell metric commands, Linux/macOS use `cat/free/df`. A failed metric never marks
the device unreachable — the live tunnel proves reachability.

**Global Settings → Agent:**
- `agent_heartbeat` (s, default 15) — interval the agent pings the server (keeps idle
  WebSocket alive behind proxies).
- `agent_reconnect` (s, default 5) — backoff before retrying after a dropout.

**Global Settings → General:**
- `theme` — UI appearance (`dark` / `light` / `premium`), stored server-side (not just
  localStorage) so it follows the admin's choice on every device.
- `session_retention_days` (default 90, `0` = keep forever) — session-log retention;
  old `session_logs` rows are purged by the scheduler loop.

### 7.12 Public Dashboard
Read-only page at `/public` (no login). Host IPs are masked (e.g. `10.0.0.x`).

### 7.13 Network Topology
Live LAN scanner: auto-derives the subnet from the first registered device's IP,
discovers hosts via TCP probe (no ICMP/ping — works in Docker without `CAP_NET_RAW`),
draws an interactive graph (pan/zoom), with per-node detail (hostname, type, ports,
latency, MAC+vendor, web title, banner). **Admin-only**, every scan is written to the
audit log. Supports **custom scans** (specific subnet + ports).

### 7.14 Security
- Credentials encrypted at rest (Fernet, key derived from `SECRET_KEY`).
- Password hashing: PBKDF2-HMAC-SHA256 (200k iterations) + per-user salt, constant-time compare.
- 2FA TOTP (RFC 6238, no external dependency).
- Login rate-limiting per IP.
- OIDC auto-provision OFF by default (no silent account creation).
- Public dashboard IP masking.
- SSRF protection on upload-from-URL.
- Session audit logging (every shell session is recorded).

### 7.15 PWA & Mobile
Install to phone home screen; mobile UI (cards + bottom nav, swipeable). The mobile
terminal has an on-screen keyboard (Ctrl/Alt/Shift + a–z row). Every menu has a
consistent header (14px padding, border-bottom, min-height 52px).

---

## 8. API Reference

Base: `http://<host>:8000`. All `/api/*` endpoints require an
`Authorization: Bearer <token>` header except auth and public.

### Auth (`/api/auth/*`)
| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/auth/login` | `{username,password,〔totp〕}` → token |
| POST | `/api/auth/register` | register (if registration-open) |
| GET | `/api/auth/me` | current user profile |
| GET | `/api/auth/registration-open` | whether registration is open |
| POST | `/api/auth/change-password` | change password |
| GET/POST | `/api/auth/2fa/status` · `/2fa/setup` · `/2fa/qr` · `/2fa/disable` | 2FA TOTP |

### Devices (`/api/devices/*`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/devices/` | list devices (RBAC-filtered) |
| POST | `/api/devices/` | add device |
| PUT | `/api/devices/{id}` | edit |
| DELETE | `/api/devices/{id}` | delete |
| POST | `/api/devices/import` | bulk import |
| GET | `/api/devices/{id}` | detail |
| GET | `/api/devices/{id}/status` | live status (CPU/mem/disk) |
| POST | `/api/devices/{id}/run` | run command |
| POST | `/api/devices/{id}/action` | action (start/stop/restart) |
| GET | `/api/devices/tailscale/discover` | discover Tailscale |

### Terminal (`/api/terminal/*`)
- `WebSocket /api/terminal/{id}` — interactive shell
- `WebSocket /terminal/{id}` — (alias)

### Files / SFTP (`/api/files/*`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/files/{id}/browse?path=` | list directory |
| POST | `/api/files/{id}/read` | read file |
| POST | `/api/files/{id}/write` | write file |
| POST | `/api/files/{id}/upload` | upload (progress) |
| POST | `/api/files/{id}/upload-link` | upload-from-URL (SSRF-guarded) |
| GET | `/api/files/{id}/download?path=` | download |
| POST | `/api/files/{id}/mkdir` | create folder |

### Docker (`/api/docker/*`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/docker/{id}/containers` | list containers |
| GET | `/api/docker/{id}/stats` | stats |
| GET | `/api/docker/{id}/logs/{container_id}` | logs |
| POST | `/api/docker/{id}/action` | start/stop/restart/exec |

### Snippets (`/api/snippets/*`)
`GET /` · `POST /` · `PUT /{id}` · `DELETE /{id}`

### Bulk (`/api/bulk`)
`PUT /` (mass edit/delete) · `DELETE /` (mass delete) · `POST /run` (run on many)

### Scheduled (`/api/scheduled/*`)
`GET /` · `POST /` · `PUT /{id}` · `DELETE /{id}` · `POST /{id}/run` (manual run)

### Users (`/api/users/*`) — admin only
`GET /` · `POST /{id}/role` (change role) · `PUT /{id}` · `DELETE /{id}`

### Settings (`/api/settings/*`)
`GET /status` · `POST /` (save notifications + monitor interval + public_dashboard)
· `GET /audit` (audit log) · `GET /telegram/chatid` · `GET /about`

### Home (`/api/home/*`)
`GET /summary` (stat cards, device health, recent activity)

### Topology (`/api/topology/*`)
`GET /scan?subnet=&ports=` (custom scan, admin-only, audit-logged)

### Agents (`/api/agents/*`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/agents/` | list agents (with `connected`, `ips`, `os`, `device_id`) |
| POST | `/api/agents/` | create agent + token (`device_id` optional) |
| PUT | `/api/agents/{agent_id}` | rename / link-unlink to a device (`device_id`) |
| DELETE | `/api/agents/{agent_id}` | remove agent |
| GET | `/api/agents/{agent_id}/bootstrap` | copy-paste helpers (one-liner / PowerShell / sh / ps1) |
| WebSocket | `/api/agents/ws?token=` | outbound tunnel from the device |
| WebSocket | `/api/agents/terminal/{device_id}` | interactive shell through the agent |
| POST | `/api/agents/fs/{device_id}` | file manager op (list/read/write/mkdir/delete/stat/exec) through the agent |

### Public (`/api/public/*`)
`GET /status` (read-only, IP masked) — if `public_dashboard=true`

### OIDC (`/api/oidc/*`)
`GET /enabled` · `GET /login` · `GET /callback`

### Backup (`/api/backup/*`)
`GET /export` (export DB) · `GET /inventory/{fmt}` (device inventory)

---

## 9. Timezone (important note)

The backend stores all times as **UTC** (`datetime.now(timezone.utc)`).
The frontend renders them in the device's local TZ:
- Home & Session History use the `_asUTC()` helper, which appends `Z` to the naive ISO
  string so `toLocaleString()` renders correctly (WIB on Vandex's device, not +7 hours off).
- `TZ=Asia/Jakarta` in docker-compose affects **only** the container shell, not the
  application timestamps (the app hardcodes UTC).

---

## 10. Testing

```bash
pytest        # auth · RBAC · devices · tags · bulk · docker · settings · alerts · scheduled
```
Tests cover auth, RBAC, device CRUD, tags, bulk, docker, settings, alerts, scheduled.

---

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Topology scan empty | Ensure `network_mode: host` is active; check LAN firewall |
| Terminal drops | Raise `proxy_read_timeout` in the reverse proxy (≥3600s) |
| Login keeps failing (429) | Wait out the 15-min window, or reset `_LOGIN_MAX_FAILS` |
| Credentials fail on SSH connect | Check `SSH_IGNORE_KNOWN_HOSTS` (set `true` for homelab) |
| 2FA not showing | Ensure TOTP secret is set (`/api/auth/2fa/setup`) |
| File unreadable in Docker | Check `./data` volume is mounted |
| Public dashboard 403 | Set `public_dashboard=true` in Settings |

---

## 12. Roadmap

**Shipped:** terminal search, session re-run, command palette, upload progress,
snippet categories, app identity, Network Topology + custom scan, Windows monitoring
(auto OS detect + PowerShell), Agent IP discovery (+ auto-link), Agent bootstrap helpers,
agent-aware monitoring & shell (agent-only devices no longer show unreachable), Global
Settings (theme, session retention, agent heartbeat & reconnect).

**Planned:** resource history graphs, per-device public share links, API bot tokens,
password reset flow, mobile terminal touch optimisations.

---

## 13. Contributing & Community

- Discussion: https://t.me/ShellDeck
- Issues: https://github.com/Vandexuzu/ShellDeck/issues
- Contributions must keep the **$0, self-hosted** principle (agentless by default,
  optional agents only for NAT/firewall hosts). Run `pytest` before opening a PR.

**Author:** Vandexuzu · https://github.com/Vandexuzu · MIT License
