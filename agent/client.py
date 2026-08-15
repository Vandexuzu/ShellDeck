#!/usr/bin/env python3
"""ShellDeck reverse-tunnel agent — FULL RELAY client.

Run this ON the remote device behind a NAT/firewall. It opens an *outbound*
WebSocket to your ShellDeck server and relays an interactive shell (PTY) for
each operator session.

Requirements:
    pip install websocket-client

Usage:
    export SHELLDECK_URL="http://YOUR_SERVER:8000"
    export SHELLDECK_AGENT_TOKEN="YOUR_TOKEN"
    python3 client.py

Or:
    python3 client.py --url http://YOUR_SERVER:8000 --token YOUR_TOKEN

Notes:
    * Each operator session runs in a real PTY (pty.openpty + the shell from
      $SHELL or /bin/bash) so arrows, tab-completion and colour all work.
    * websocket-client's WebSocketApp drives I/O on its own thread; we only ever
      CALL ws.send_text() from worker threads and READ frames inside on_message,
      which keeps the library happy (calling ws.recv() from a callback thread is
      unsafe). Per-session input is passed via an in-process queue.
"""
from __future__ import annotations

import argparse
import json
import os
import select
import struct
import sys
import threading
import time

try:
    import websocket
except ImportError:
    sys.exit("Missing dependency: pip install websocket-client")

# POSIX-only modules (PTY/termios/fcntl). Guard so the agent also runs on Windows.
IS_WINDOWS = sys.platform.startswith("win")
if not IS_WINDOWS:
    import pty
    import termios
    import fcntl


# Heartbeat + reconnect tunables (overridable via env, pushed from server settings).
HEARTBEAT_INTERVAL = float(os.environ.get("SHELLDECK_HEARTBEAT", "15"))
RECONNECT_DELAY = float(os.environ.get("SHELLDECK_RECONNECT", "5"))


def _nonblock(fd: int) -> None:
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except Exception:
        pass


class Session:
    """An interactive shell session relayed over the agent WebSocket.

    On POSIX a real PTY is used (arrows/tab-completion/colour all work). On
    Windows we fall back to a piped `cmd.exe` console (no PTY available), which
    still relays input/output correctly.
    """

    def __init__(self, ws, cid: int, cols: int, rows: int, init_cmd: str):
        import queue
        self.ws = ws
        self.cid = cid
        self.queue: queue.Queue = queue.Queue()
        self.alive = True
        if not IS_WINDOWS:
            self._open_posix(init_cmd, cols, rows)
        else:
            self._open_windows(init_cmd)

    # ---- POSIX (PTY) -------------------------------------------------------
    def _open_posix(self, init_cmd: str, cols: int, rows: int) -> None:
        shell = os.environ.get("SHELL", "/bin/bash")
        master_fd, slave_fd = pty.openpty()
        _nonblock(master_fd)
        pid = os.fork()
        if pid == 0:
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(master_fd)
            try:
                os.execv(shell, [shell, "-i"])
            except Exception:
                os._exit(127)
        os.close(slave_fd)
        self.master_fd = master_fd
        self.pid = pid
        self.proc = None
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass
        if init_cmd:
            try:
                os.write(self.master_fd, (init_cmd + "\n").encode())
            except OSError:
                pass
        self.thread = threading.Thread(target=self._run_posix, daemon=True)
        self.thread.start()

    def _run_posix(self) -> None:
        try:
            while self.alive:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if self.master_fd in r:
                    try:
                        data = os.read(self.master_fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    try:
                        self.ws.send_text(json.dumps({"t": "data", "cid": self.cid, "data": data.decode(errors="replace")}))
                    except Exception:
                        break
                while not self.queue.empty():
                    try:
                        item = self.queue.get_nowait()
                    except Exception:
                        break
                    if item is None:
                        self.alive = False
                        break
                    mtype = item.get("t")
                    if mtype == "data":
                        try:
                            os.write(self.master_fd, item.get("data", "").encode(errors="replace"))
                        except OSError:
                            pass
                    elif mtype == "resize":
                        try:
                            winsize = struct.pack("HHHH", item.get("rows", 24), item.get("cols", 80), 0, 0)
                            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
                        except Exception:
                            pass
                    elif mtype == "kill":
                        self.alive = False
                        break
        finally:
            try:
                self.ws.send_text(json.dumps({"t": "exit", "cid": self.cid, "code": 0}))
            except Exception:
                pass
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            try:
                import signal
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                pass

    # ---- Windows (piped cmd.exe) ------------------------------------------
    def _open_windows(self, init_cmd: str) -> None:
        import subprocess
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        self.proc = subprocess.Popen(
            [comspec],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            bufsize=0,
        )
        self.master_fd = None
        self.pid = self.proc.pid
        if init_cmd and self.proc.stdin:
            try:
                self.proc.stdin.write((init_cmd + "\n").encode("utf-8", errors="replace"))
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass
        self.thread = threading.Thread(target=self._run_windows, daemon=True)
        self.thread.start()

    def _run_windows(self) -> None:
        import io
        try:
            while self.alive and self.proc and self.proc.stdout:
                data = self.proc.stdout.read(65536)
                if not data:
                    break
                try:
                    text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else data
                    self.ws.send_text(json.dumps({"t": "data", "cid": self.cid, "data": text}))
                except Exception:
                    break
                while not self.queue.empty():
                    try:
                        item = self.queue.get_nowait()
                    except Exception:
                        break
                    if item is None or item.get("t") == "kill":
                        self.alive = False
                        break
                    if item.get("t") == "data" and self.proc.stdin:
                        try:
                            self.proc.stdin.write(item.get("data", "").encode("utf-8", errors="replace"))
                            self.proc.stdin.flush()
                        except (OSError, ValueError):
                            pass
        finally:
            try:
                self.ws.send_text(json.dumps({"t": "exit", "cid": self.cid, "code": 0}))
            except Exception:
                pass
            try:
                if self.proc:
                    self.proc.terminate()
            except Exception:
                pass

    # ---- shared API --------------------------------------------------------
    def send(self, item: dict) -> None:
        self.queue.put(item)

    def resize(self, cols: int, rows: int) -> None:
        # Only meaningful on POSIX (PTY winsize). Windows console auto-tracks.
        if not IS_WINDOWS:
            self.queue.put({"t": "resize", "cols": cols, "rows": rows})

    def kill(self) -> None:
        self.queue.put({"t": "kill"})


class AgentClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.ws = None
        self.sessions: dict[int, Session] = {}
        self.lock = threading.Lock()

    def _collect_ips(self) -> list[str]:
        """Return the device's local interface IP addresses (IPv4), skipping
        loopback and link-local. Cross-platform: `ip -br addr` (Linux) and
        `ipconfig` (Windows)."""
        import re
        import subprocess
        ips: list[str] = []
        try:
            out = subprocess.run(["ip", "-br", "addr"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                # e.g. eth0  UP  192.168.1.10/24  ...
                m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", line)
                if m:
                    ip = m.group(1)
                    if not ip.startswith(("127.", "169.254.")):
                        ips.append(ip)
        except Exception:
            pass
        if not ips:
            try:
                out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10).stdout
                for m in re.finditer(r"IPv4 Address[.\s]*: (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", out):
                    ip = m.group(1)
                    if not ip.startswith(("127.", "169.254.")):
                        ips.append(ip)
            except Exception:
                pass
        # De-dup while preserving order.
        seen = set()
        return [x for x in ips if not (x in seen or seen.add(x))]

    def _report_ips(self):
        try:
            ips = self._collect_ips()
            if ips and self.ws is not None:
                self.ws.send_text(json.dumps({"t": "ips", "token": self.token, "ips": ips}))
        except Exception:
            pass

    def on_open(self, ws):
        print(f"[agent] connected to {self.url}.")
        self._report_ips()
        # Periodic heartbeat so proxies/load-balancers don't drop the idle socket.
        def _hb():
            import threading
            while True:
                try:
                    if ws.sock is not None and ws.sock.connected:
                        ws.send_text(json.dumps({"t": "hb"}))
                except Exception:
                    pass
                time.sleep(HEARTBEAT_INTERVAL)
        threading.Thread(target=_hb, daemon=True).start()

    def on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        t = msg.get("t")
        cid = msg.get("cid")
        if t == "exec":
            with self.lock:
                if cid in self.sessions:
                    return
                self.sessions[cid] = Session(ws, cid, msg.get("cols", 80), msg.get("rows", 24), msg.get("data", ""))
        elif t == "fs":
            self._handle_fs(ws, msg)
        elif cid is not None:
            with self.lock:
                sess = self.sessions.get(cid)
            if sess is not None:
                sess.send(msg)

    # ----- File-system relay (for devices behind NAT reached via agent) -----
    def _handle_fs(self, ws, msg):
        import os as _os
        import shutil
        import stat as _stat

        cid = msg.get("cid")
        op = msg.get("op")
        path = msg.get("path", "/")
        try:
            result = None
            if op == "list":
                entries = []
                for name in sorted(_os.listdir(path)):
                    full = _os.path.join(path.rstrip("/"), name)
                    try:
                        st = _os.stat(full)
                        is_dir = _stat.S_ISDIR(st.st_mode)
                    except OSError:
                        is_dir = False
                        st = None
                    entries.append({
                        "name": name,
                        "path": full,
                        "is_dir": is_dir,
                        "size": st.st_size if st else 0,
                        "mtime": int(st.st_mtime) if st else 0,
                    })
                entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
                result = entries
            elif op == "read":
                with open(path, "r", errors="replace") as f:
                    data = f.read(2_000_000)
                result = {"content": data}
            elif op == "read_b64":
                with open(path, "rb") as f:
                    raw = f.read(2_000_000)
                import base64 as _b64
                result = {"content": _b64.b64encode(raw).decode("ascii")}
            elif op == "write":
                content = msg.get("data", "")
                with open(path, "w") as f:
                    f.write(content)
                result = {"written": len(content)}
            elif op == "write_b64":
                import base64 as _b64
                raw = _b64.b64decode(msg.get("data", ""))
                with open(path, "wb") as f:
                    f.write(raw)
                result = {"written": len(raw)}
            elif op == "mkdir":
                _os.makedirs(path, exist_ok=True)
                result = {"created": True}
            elif op == "delete":
                if _os.path.isdir(path) and not _os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    _os.remove(path)
                result = {"deleted": True}
            elif op == "stat":
                st = _os.stat(path)
                result = {"size": st.st_size}
            elif op == "exec":
                # One-shot command execution for server-side monitoring/relay.
                # Returns combined stdout (truncated) so the server can parse metrics.
                import subprocess as _sp
                try:
                    proc = _sp.run(msg.get("data", ""), shell=True, capture_output=True, text=True, timeout=25)
                    out = (proc.stdout or "") + (proc.stderr or "")
                except Exception as exc:
                    out = f"exec error: {exc}"
                result = {"output": out[:20000]}
            else:
                raise ValueError(f"unknown fs op: {op}")
            ws.send_text(json.dumps({"t": "fs", "cid": cid, "ok": True, "result": result}))
        except Exception as exc:
            ws.send_text(json.dumps({"t": "fs", "cid": cid, "ok": False, "err": f"{type(exc).__name__}: {exc}"}))

    def run(self) -> None:
        while True:
            try:
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self.on_open,
                    on_close=lambda ws, *a: print("[agent] disconnected."),
                    on_message=self.on_message,
                )
                self.ws.run_forever()
            except Exception as e:
                print("[agent] error:", e)
            print("[agent] reconnecting in 5s ...")
            time.sleep(RECONNECT_DELAY)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("SHELLDECK_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=os.environ.get("SHELLDECK_AGENT_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:
        sys.exit("Set SHELLDECK_AGENT_TOKEN (or --token).")
    ws_url = args.url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/api/agents/ws?token=" + args.token
    print(f"[agent] connecting to {args.url} ...")
    AgentClient(ws_url, args.token).run()


if __name__ == "__main__":
    main()
