#!/usr/bin/env bash
# ShellDeck agent installer — SELF-ENROLL (no per-device token on the cmdline).
# This generic script only carries the server URL and a *revocable* enrollment
# secret. On first run the agent mints its own per-device token from the server
# and stores it locally (root-only). Nothing secret is ever in the process list,
# shell history, or the install URL.
#
# PREREQUISITES on the target device:
#   * Python 3.7+ with pip/pip3 available
#   * outbound network access to the ShellDeck server (WebSocket, port 80/443)
#   * this script auto-installs the only Python dependency: websocket-client
#     (falls back to python3-websocket / apt if the first install source fails)
#   * Linux/macOS: python3 on PATH  |  Windows: run PowerShell AS ADMINISTRATOR
#
# Run:
#   curl -fsSL YOUR_SERVER/install.sh | bash
#
# or with overrides:
#   SHELLDECK_URL='https://shelldeck.example.com' \
#   SHELLDECK_ENROLL_SECRET='<enroll-secret>' \
#   curl -fsSL YOUR_SERVER/install.sh | bash
set -euo pipefail

URL="${SHELLDECK_URL:-__URL__}"
SECRET="${SHELLDECK_ENROLL_SECRET:-__SECRET__}"
HB="${SHELLDECK_HEARTBEAT:-__HB__}"
RC="${SHELLDECK_RECONNECT:-__RC__}"
NAME="${SHELLDECK_AGENT_NAME:-$(hostname 2>/dev/null || echo enrolled-device)}"

echo ">> Installing ShellDeck agent from ${URL}"

# Resolve a working pip (pip / pip3 / python3 -m pip) — some images only ship one.
PIP=""
if command -v pip >/dev/null 2>&1; then PIP="pip";
elif command -v pip3 >/dev/null 2>&1; then PIP="pip3";
elif command -v python3 >/dev/null 2>&1; then PIP="python3 -m pip"; fi

# Install the only runtime dependency (websocket-client). Try multiple sources so a
# single failure (e.g. offline PyPI, or a distro-packaged name) doesn't abort:
#   1. PyPI package  : websocket-client
#   2. alt PyPI name : python3-websocket
#   3. system package: python3-websocket (apt, Linux only)
install_ws() {
  if [ -n "$PIP" ]; then
    $PIP install --quiet websocket-client 2>/dev/null && return 0
    $PIP install --quiet python3-websocket 2>/dev/null && return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq 2>/dev/null && apt-get install -y -qq python3-websocket 2>/dev/null && return 0
  fi
  return 1
}

if ! python3 -c "import websocket" >/dev/null 2>&1; then
  install_ws || true
fi

# Final check: the import must resolve, regardless of which source provided it.
if ! python3 -c "import websocket" >/dev/null 2>&1; then
  echo "!! Failed to install the 'websocket-client' dependency." >&2
  echo "   Try manually:  $PIP install websocket-client" >&2
  echo "   or (Debian/Ubuntu):  apt-get install python3-websocket" >&2
  exit 1
fi
echo ">> Dependency websocket-client ready."

curl -fsSL "${URL}/agent_client" -o /opt/shelldeck_agent.py

# Store config (NOT the token — the agent fetches+persists that itself) in a
# restricted env file, kept out of the unit file and the process list.
cat > /etc/shelldeck-agent.env <<ENV
SHELLDECK_URL='${URL}'
SHELLDECK_ENROLL_SECRET='${SECRET}'
SHELLDECK_AGENT_NAME='${NAME}'
SHELLDECK_HEARTBEAT='${HB}'
SHELLDECK_RECONNECT='${RC}'
ENV
chmod 600 /etc/shelldeck-agent.env

cat > /etc/systemd/system/shelldeck-agent.service <<UNIT
[Unit]
Description=ShellDeck Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/shelldeck-agent.env
ExecStart=/usr/bin/env python3 /opt/shelldeck_agent.py --enroll-secret '${SECRET}' --name '${NAME}'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now shelldeck-agent
echo ">> Done. The device will appear as a PENDING agent in ShellDeck — claim it from the Agents tab. Status: systemctl status shelldeck-agent"
