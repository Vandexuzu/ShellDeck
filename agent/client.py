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
import pty
import select
import struct
import sys
import termios
import threading
import time

try:
    import websocket
except ImportError:
    sys.exit("Missing dependency: pip install websocket-client")

HEARTBEAT_INTERVAL = 15.0


def open_pty_shell() -> tuple[int, int, list]:
    """Spawn a login shell in a fresh PTY (thread-safe). Returns (master_fd, pid, argv)."""
    shell = os.environ.get("SHELL", "/bin/bash")
    master_fd, slave_fd = pty.openpty()
    try:
        import fcntl
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except Exception:
        pass
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
    return master_fd, pid, [shell]


class Session:
    def __init__(self, ws, cid: int, cols: int, rows: int, init_cmd: str):
        import queue
        self.ws = ws
        self.cid = cid
        self.master_fd, self.pid, _ = open_pty_shell()
        self.queue: queue.Queue = queue.Queue()
        self.alive = True
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            import fcntl
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass
        if init_cmd:
            try:
                os.write(self.master_fd, (init_cmd + "\n").encode())
            except OSError:
                pass
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
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
                # Drain operator input queued from on_message.
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
                            import fcntl
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


class AgentClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.ws = None
        self.sessions: dict[int, Session] = {}
        self.lock = threading.Lock()

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
        elif cid is not None:
            with self.lock:
                sess = self.sessions.get(cid)
            if sess is not None:
                sess.queue.put(msg)

    def run(self) -> None:
        while True:
            try:
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_open=lambda ws: print("[agent] connected."),
                    on_close=lambda ws, *a: print("[agent] disconnected."),
                    on_message=self.on_message,
                )
                self.ws.run_forever()
            except Exception as e:
                print("[agent] error:", e)
            print("[agent] reconnecting in 5s ...")
            time.sleep(5)


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
