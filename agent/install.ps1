# ShellDeck agent installer - SELF-ENROLL (no per-device token on the cmdline).
# Generic script: only the server URL + a *revocable* enrollment secret. The
# agent mints its own per-device token on first run and stores it locally.
#
# PREREQUISITES on the target device:
#   * Python 3.7+ with pip available
#   * outbound network access to the ShellDeck server (WebSocket, port 80/443)
#   * this script auto-installs the only Python dependency: websocket-client
#   * Run this script in PowerShell AS ADMINISTRATOR
#
# Run in PowerShell AS ADMINISTRATOR:
#   Invoke-WebRequest -Uri 'YOUR_SERVER/install.ps1' -OutFile install.ps1; .\install.ps1
$ErrorActionPreference = "Stop"

# Install into a protected-but-writable system dir so we never write to the
# current working directory (e.g. C:\WINDOWS\system32 when launched from there).
$INSTALL_DIR = Join-Path $env:ProgramData "ShellDeck"
if (-not (Test-Path $INSTALL_DIR)) { New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null }
Set-Location $INSTALL_DIR

$URL = $env:SHELLDECK_URL
if (-not $URL) { $URL = "__URL__" }
$SECRET = $env:SHELLDECK_ENROLL_SECRET
if (-not $SECRET) { $SECRET = "__SECRET__" }
$HB = $env:SHELLDECK_HEARTBEAT
if (-not $HB) { $HB = "__HB__" }
$RC = $env:SHELLDECK_RECONNECT
if (-not $RC) { $RC = "__RC__" }
$NAME = $env:SHELLDECK_AGENT_NAME
if (-not $NAME) { $NAME = $env:COMPUTERNAME }

Write-Host ">> Installing into $INSTALL_DIR"
Write-Host ">> Downloading client from $URL"
Invoke-WebRequest -Uri "$URL/agent_client" -OutFile shelldeck_agent.py
pip install websocket-client
if (-not $?) {
    pip3 install websocket-client
}

# Store config (NOT the token - the agent fetches+persists that itself).
# Write values WITHOUT surrounding quotes and as plain ASCII (no BOM) so the
# cross-platform .env parser in client.py reads them cleanly on Windows too.
$envLines = @(
    "SHELLDECK_URL=$URL",
    "SHELLDECK_ENROLL_SECRET=$SECRET",
    "SHELLDECK_AGENT_NAME=$NAME",
    "SHELLDECK_HEARTBEAT=$HB",
    "SHELLDECK_RECONNECT=$RC"
)
Set-Content -Path shelldeck-agent.env -Encoding ASCII -Value ($envLines -join "`r`n")
icacls shelldeck-agent.env /inheritance:r /grant:r "$env:USERNAME`:(R)" | Out-Null

$exe = (Get-Command python).Source
# NOTE: -Argument is a single string, so any quotes inside it are passed LITERALLY
# to Python (unlike bash). URL/secret have no spaces, so we omit quotes entirely;
# the name is wrapped in escaped double-quotes only if it contains spaces.
$nameArg = if ($NAME -match '\s') { "`"$NAME`"" } else { $NAME }
$act = New-ScheduledTaskAction -Execute $exe -Argument "shelldeck_agent.py --url $URL --enroll-secret $SECRET --name $nameArg" -WorkingDirectory $INSTALL_DIR
$trig = New-ScheduledTaskTrigger -AtStartup
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'ShellDeckAgent' -Action $act -Trigger $trig -Settings $set -RunLevel Highest -Force
Start-ScheduledTask -TaskName 'ShellDeckAgent'
Write-Host ">> Installed as Scheduled Task. The device appears as a PENDING agent - claim it from the Agents tab in ShellDeck. Check: taskschd.msc"
