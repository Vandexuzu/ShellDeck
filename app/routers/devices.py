"""CRUD endpoints for managed devices."""
from __future__ import annotations

import json
import socket
import subprocess as _sp
from datetime import datetime, timezone

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt, settings
from app.db import get_db
from app.models import AuditLog, Device, SessionLog, TopologySnapshot, User
from app.schemas import DeviceCreate, DeviceOut, DeviceUpdate
from app.security import get_current_user, operator_only, admin_only
from app.audit import log_audit

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _admin_user(db: Session) -> User | None:
    """The primary admin (is_admin, lowest id)."""
    return db.scalar(select(User).where(User.is_admin).order_by(User.id))


def _visible_devices(db: Session, user: User):
    """Devices a user may *see* (read-only cards / status).

    - admin:   every device
    - viewer:  every device
    - operator: the admin's shared fleet + the operator's own devices
    """
    if user.role in ("admin", "viewer"):
        return select(Device).order_by(Device.name)
    admin = _admin_user(db)
    admin_id = admin.id if admin else -1
    return select(Device).where(
        (Device.owner_id == admin_id) | (Device.owner_id == user.id)
    ).order_by(Device.name)


def _can_view(db: Session, device: Device, user: User) -> bool:
    """Whether the user may *view* a device (status, file browse, docker read).

    Admin and viewer may view any device; an operator may view the admin's
    fleet and their own devices.
    """
    if user.role in ("admin", "viewer"):
        return True
    admin = _admin_user(db)
    return device.owner_id == user.id or (admin is not None and device.owner_id == admin.id)


def _can_access(db: Session, device: Device, user: User) -> bool:
    """Whether the user may *act on* a device (shell, sftp write, docker, run).

    Admin: any device. Operator: only their own. Viewer: never.
    """
    if user.role == "admin":
        return True
    if user.role == "operator":
        return device.owner_id == user.id
    return False


def _owned(db: Session, device_id: int, user: User) -> Device:
    """Resolve a device the user is allowed to act on (admin: any, operator: own)."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if user.role == "admin":
        return device
    if user.role == "operator" and device.owner_id == user.id:
        return device
    raise HTTPException(status_code=404, detail="Device not found")


def _to_out(device: Device, db: Session | None = None) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    # Mark whether a live agent is linked to this device (for agent-terminal UI).
    if db is not None:
        from app.models import Agent
        from app.routers.agents import _LIVE
        try:
            agent = db.scalar(select(Agent).where(Agent.device_id == device.id))
            out.has_agent = bool(agent and agent.token in _LIVE)
        except Exception:
            out.has_agent = False
    return out


@router.get("", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Device]:
    return list(db.scalars(_visible_devices(db, user)))


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Device:
    # Operators own their own devices; admins own the shared fleet.
    owner_id = user.id
    device = Device(
        owner_id=owner_id,
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        auth_method=payload.auth_method,
        password_enc=encrypt(payload.password or ""),
        private_key_enc=encrypt(payload.private_key or ""),
        os=payload.os,
        notes=payload.notes,
        bastion_id=payload.bastion_id,
        tags=payload.tags,
        tailscale=payload.tailscale,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    log_audit(db, user, "device_create", f"name={device.name} host={device.host}:{device.port} auth={device.auth_method}"
              + (f" bastion_id={device.bastion_id}" if device.bastion_id else ""))
    return _to_out(device, db)


@router.get("/generate-key")
def generate_ssh_key(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Generate an ed25519 SSH keypair and return both private + public key (PEM)."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    kp = ed25519.Ed25519PrivateKey.generate()
    priv = kp.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = kp.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return {"private_key": priv, "public_key": pub}


@router.get("/tailscale/discover")
def tailscale_discover(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Discover Tailscale devices on the local network via `tailscale status --json`.

    Returns a list of nodes (name, ip, hostname, os) that are not yet added as
    ShellDeck devices. Requires the `tailscale` CLI on the host (the ShellDeck
    server box, e.g. your Tailscale node).
    """
    import json as _json
    import shutil
    import subprocess

    if shutil.which("tailscale") is None:
        return {"available": False, "nodes": [], "error": "tailscale CLI not found on host"}
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return {"available": True, "nodes": [], "error": out.stderr.strip()[:200]}
        data = _json.loads(out.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "nodes": [], "error": str(exc)[:200]}

    known_hosts = {d.host for d in db.scalars(_visible_devices(db, user)).all()}
    nodes = []
    # `Self` and `Peer` maps keyed by IP.
    for section in ("Self", "Peer"):
        for ip, node in (data.get(section) or {}).items():
            if ip in known_hosts:
                continue
            nodes.append({
                "ip": ip,
                "name": node.get("HostName") or node.get("DisplayName") or ip,
                "hostname": node.get("DNSName", "").rstrip(".") or None,
                "os": node.get("OS", "") or "",
                "online": bool(node.get("Online", False)),
            })
    return {"available": True, "nodes": nodes}


def _classify_device(ports: list[int]) -> str:
    """Infer a friendly device type from its open ports (signature-based)."""
    pset = set(ports)
    if 8123 in pset:
        return "Home Assistant"
    if 3389 in pset:
        return "Windows / RDP"
    if 445 in pset or 139 in pset:
        return "File Share (SMB)"
    if 2049 in pset:
        return "NFS Share"
    if 53 in pset and 67 in pset:
        return "Router / DHCP"
    if 53 in pset:
        return "DNS Server"
    if 80 in pset or 443 in pset or 8080 in pset or 8443 in pset:
        if 22 in pset:
            return "Server / Web"
        return "Web Device"
    if 22 in pset and 23 in pset:
        return "Network Gear"
    if 22 in pset:
        return "SSH Host"
    if 554 in pset or 8554 in pset:
        return "Camera (RTSP)"
    if ports:
        return "Unknown Service"
    return "Host"


def _grab_banner(ip: str, port: int, timeout: float = 0.5) -> str:
    """Connect and read a short service banner (SSH/HTTP/etc)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex((ip, port)) != 0:
                return ""
            try:
                sock.sendall(b"\r\n")
            except OSError:
                pass
            try:
                data = sock.recv(256)
            except OSError:
                return ""
            text = data.decode("utf-8", "ignore").replace("\r", " ").replace("\n", " ").strip()
            return text[:80]
    except OSError:
        return ""


def _http_title(ip: str, port: int, timeout: float = 0.6) -> str:
    """Fetch the <title> of an HTTP(S) service."""
    import http.client

    scheme = "https" if port in (443, 8443) else "http"
    try:
        conn = (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(ip, port, timeout=timeout)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read(4096).decode("utf-8", "ignore")
        conn.close()
        import re

        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()[:80]
    except Exception:
        pass
    return ""


# Minimal OUI → vendor prefix map (most common home/enterprise vendors).
_OUI_MAP = {
    "00:50:56": "VMware", "00:0c:29": "VMware", "08:00:27": "VirtualBox",
    "00:1a:11": "Google", "f4:ca:e5": "Google", "3c:5a:b4": "Google",
    "ac:bc:32": "Apple", "a4:83:e7": "Apple", "f0:18:98": "Apple", "d0:03:4b": "Apple",
    "3c:22:fb": "Apple", "00:25:00": "Apple",
    "54:ef:44": "Xiaomi", "64:09:80": "Xiaomi", "58:44:98": "Xiaomi", "34:ce:00": "Xiaomi",
    "b0:25:aa": "Xiaomi", "d4:61:9d": "Xiaomi",
    "9c:b6:d0": "Samsung", "8c:dc:d4": "Samsung", "5c:0a:5b": "Samsung", "40:2c:f4": "Samsung",
    "3c:71:bf": "TP-Link", "50:c7:bf": "TP-Link", "60:32:b1": "TP-Link", "e8:65:d4": "TP-Link",
    "24:a4:3c": "Huawei", "ac:74:09": "Huawei", "c4:93:d9": "Huawei",
    "f8:32:e4": "ASUS", "04:d9:f5": "ASUS", "10:7b:44": "ASUS", "18:d6:c7": "ASUS",
    "f4:6d:04": "Netgear", "c0:ff:d4": "Netgear", "b0:7f:b9": "Netgear",
    "00:11:32": "Synology", "00:11:22": "Synology", "00:0c:07": "Synology",
    "00:d0:63": "Intel", "f4:8e:38": "Intel", "00:1f:3b": "Intel",
    "00:1b:a9": "Dell", "d4:be:d9": "Dell", "f0:4d:a2": "Dell",
    "00:1d:09": "Cisco", "00:21:1b": "Cisco", "68:ef:bd": "Cisco",
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "2c:cf:67": "Raspberry Pi",
    "00:13:10": "Linksys", "00:18:f8": "Linksys",
    "00:26:37": "Ubiquiti", "fc:ec:da": "Ubiquiti", "24:5a:4c": "Ubiquiti",
    "d0:7e:28": "Sonos", "5c:aa:fd": "Sonos", "94:9f:3e": "Sonos",
    "68:37:e9": "Amazon/Echo", "44:65:0d": "Amazon/Echo", "a0:02:dc": "Amazon/Echo",
    "cc:41:9f": "Chromecast", "ec:ad:b8": "Chromecast", "54:60:09": "Chromecast",
    "00:11:22": "MikroTik", "4c:5e:0c": "MikroTik", "d4:ca:6d": "MikroTik", "64:d1:54": "MikroTik",
    "00:0c:42": "MikroTik", "b8:69:f4": "MikroTik",
    "00:14:bf": "ZTE", "00:25:11": "ZTE", "8c:97:a8": "ZTE",
    "00:09:45": "Arris", "38:60:77": "Arris", "d0:37:45": "Arris",
    "00:1d:d5": "ZyXEL", "00:24:21": "ZyXEL", "fc:d7:33": "ZyXEL",
    "00:18:82": "Atheros", "00:26:5a": "Atheros",
    "00:0f:66": "Cisco-Linksys", "c0:56:27": "Cisco-Linksys",
    "00:e0:4c": "Realtek", "52:54:00": "QEMU/KVM", "00:16:3e": "Xen",
    "00:15:5d": "Hyper-V", "02:42:ac": "Docker",
    "60:73:5c": "Amazon", "a4:8e:25": "Amazon",
    "70:3a:0e": "Google-Nest", "84:d6:d0": "Ring/Amazon",
    "00:1f:1f": "Generic/OpenWrt", "f4:f2:6a": "Ubiquiti-Edge",
}


def _lookup_vendor(mac: str) -> str:
    if not mac:
        return ""
    oui = mac.lower()[:8]
    return _OUI_MAP.get(oui, "")


def _arp_table() -> dict[str, str]:
    """Read MAC addresses from /proc/net/arp (needs host networking)."""
    table: dict[str, str] = {}
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    ip, _, _, mac = parts[0], parts[1], parts[2], parts[3]
                    if mac and mac != "00:00:00:00:00:00":
                        table[ip] = mac
    except OSError:
        pass
    return table


def _probe_mdns(ip: str, timeout: float = 0.6) -> str:
    """Best-effort mDNS/SSDP service name discovery (smart devices, IoT)."""
    import socket as _sock

    # SSDP (UPnP) discovery on the host.
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM, _sock.IPPROTO_UDP)
        s.settimeout(timeout)
        s.sendto(
            b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: ssdp:all\r\n\r\n',
            ("239.255.255.250", 1900),
        )
        try:
            data, _ = s.recvfrom(1024)
            text = data.decode("utf-8", "ignore")
            for line in text.splitlines():
                if line.lower().startswith("server:") or line.lower().startswith("location:"):
                    return line.split(":", 1)[1].strip()[:80]
        except OSError:
            pass
        finally:
            s.close()
    except OSError:
        pass
    return ""


def _measure_latency(ip: str, port: int, timeout: float = 0.4) -> float | None:
    """Round-trip connect latency in ms (best-effort)."""
    import time as _time

    try:
        start = _time.perf_counter()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex((ip, port)) != 0:
                return None
        return round((_time.perf_counter() - start) * 1000, 1)
    except OSError:
        return None


def _detect_gateway(subnet: str) -> str | None:
    """Determine the real default gateway IP for the scanned subnet.

    Tries, in order:
      1. `ip route` default gateway (most accurate, needs host networking)
      2. the .1 address of the subnet
      3. the .254 address of the subnet
    Returns an IP string or None if nothing matches the subnet.
    """
    import ipaddress

    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return None

    # 1. Default route from the host.
    try:
        out = _sp.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for token in out.split():
            try:
                if ipaddress.ip_address(token) in net:
                    return token
            except ValueError:
                continue
    except (OSError, _sp.SubprocessError):
        pass

    # 2/3. Fallback to .1 / .254 of the subnet.
    hosts = list(net.hosts())
    if hosts:
        candidate = str(hosts[0]).rsplit(".", 1)[0] + ".1"
        try:
            if ipaddress.ip_address(candidate) in net:
                return candidate
        except ValueError:
            pass
        candidate = str(hosts[0]).rsplit(".", 1)[0] + ".254"
        try:
            if ipaddress.ip_address(candidate) in net:
                return candidate
        except ValueError:
            pass
    return None


def scan_subnet(subnet: str, ports: list[int] | None = None, timeout: float = 0.3) -> dict:
    """TCP connect sweep + enriched host info for a /24 (or smaller) subnet.

    For each online host we collect: open ports, reverse-DNS hostname,
    MAC + vendor, device-type, latency, mDNS/SSDP name, web title and banners.
    Uses Python sockets (no ICMP/ping) so it works inside Docker without
    CAP_NET_RAW.
    """
    import ipaddress
    import threading

    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return {}

    if net.prefixlen < 24:
        return {}  # only /24 (or smaller) to keep scan bounded

    hosts = [str(h) for h in list(net.hosts())[:254]]
    # Ports probed to determine whether a host is alive (always a broad set so a
    # host is not falsely marked offline just because the user's requested ports
    # are closed). When the user supplies `ports`, those are *reported* but we
    # still probe the common set to detect liveness.
    detect_ports = [22, 23, 53, 80, 443, 139, 445, 8080, 8443, 3000, 3389, 554, 2049, 5432, 6379, 8123, 9000, 2375, 2376]
    common_ports = ports or detect_ports
    # The set of ports we report back: user's ports if given, else the detected ones.
    report_ports = ports if ports else detect_ports
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def worker(ip: str) -> None:
        # Liveness: probe the broad detect set (so closed user-ports don't hide a live host).
        online = False
        for port in detect_ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(timeout)
                    if sock.connect_ex((ip, port)) == 0:
                        online = True
                        break
            except OSError:
                continue
        # Report: probe exactly the ports the user asked for (or the common set).
        open_ports: list[int] = []
        for port in report_ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(timeout)
                    if sock.connect_ex((ip, port)) == 0:
                        open_ports.append(port)
            except OSError:
                continue
        with lock:
            results[ip] = {
                "status": "online" if online else "offline",
                "ports": sorted(open_ports),
                "hostname": "",
                "device_type": "",
                "mac": "",
                "vendor": "",
                "latency_ms": None,
                "mdns": "",
                "banners": {},
                "web_title": "",
            }
        if not online:
            return
        # Enrich online hosts (best-effort, failures are ignored).
        # Enrichment uses the *full* set of open ports (detect_ports) so device
        # type / web title / banners are accurate even when the user requested
        # a narrow port filter. The reported `ports` field stays as report_ports.
        all_open: list[int] = []
        if ports:
            for port in detect_ports:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                        sock.settimeout(timeout)
                        if sock.connect_ex((ip, port)) == 0:
                            all_open.append(port)
                except OSError:
                    continue
        else:
            all_open = open_ports
        try:
            host = socket.gethostbyaddr(ip)[0]
        except Exception:
            host = ""
        device_type = _classify_device(all_open)
        web_title = ""
        for port in all_open:
            if port in (80, 443, 8080, 8443):
                web_title = _http_title(ip, port, timeout)
                if web_title:
                    break
        banners = {}
        for port in (22, 80, 443, 21, 25):
            if port in all_open:
                b = _grab_banner(ip, port, timeout)
                if b:
                    banners[str(port)] = b
        # Latency: smallest connect time across open ports (first probe).
        latency = None
        for port in all_open:
            lat = _measure_latency(ip, port, timeout)
            if lat is not None:
                latency = lat
                break
        # MAC + vendor from ARP table (host networking required).
        mac = _arp_table().get(ip, "")
        vendor = _lookup_vendor(mac) if mac else ""
        # mDNS / SSDP friendly name (best-effort, IoT / smart devices).
        mdns = _probe_mdns(ip, timeout)
        with lock:
            results[ip].update({
                "hostname": host,
                "device_type": device_type,
                "web_title": web_title,
                "banners": banners,
                "mac": mac,
                "vendor": vendor,
                "latency_ms": latency,
                "mdns": mdns,
            })

    threads = [threading.Thread(target=worker, args=(ip,)) for ip in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(8.0, len(hosts) * timeout + 4))

    return results


@router.get("/topology/scan")
def topology_scan(
    subnet: str | None = None,
    ports: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict:
    """Run a live network scan on the given /24 subnet.

    Admin-only. `subnet` defaults to the LAN of the first registered device.
    `ports` is a comma-separated list of TCP ports to probe (default: common set).
    Enriches each online host with hostname, device-type, web title and banners.
    Saves a snapshot to DB and writes an audit log entry.
    """
    if not subnet or subnet.strip().lower() in ("auto", "none", ""):
        subnet = None

    if subnet is None:
        first_dev = db.scalar(select(Device).where(Device.host.isnot(None)).limit(1))
        first_host: str | None = getattr(first_dev, "host", None) if first_dev else None
        if first_host and "." in first_host:
            parts = first_host.rsplit(".", 1)
            if len(parts) == 2:
                subnet = f"{parts[0]}.0/24"
            else:
                return {"error": "Cannot derive subnet. Provide ?subnet=x.x.x.0/24 explicitly.", "nodes": []}
        else:
            return {"error": "No devices found to derive subnet from.", "nodes": []}

    parsed_ports: list[int] | None = None
    if ports:
        try:
            parsed_ports = [int(p.strip()) for p in ports.split(",") if p.strip()]
        except ValueError:
            return {"error": "Invalid ports list. Use comma-separated numbers, e.g. 22,80,443.", "nodes": []}

    # Audit: record who scanned which subnet.
    db.add(AuditLog(user_id=user.id, username=user.username, action="topology_scan",
                    detail=f"scanned subnet {subnet}" + (f" ports={ports}" if ports else ""),
                    ip=getattr(user, "last_ip", None)))

    scan = scan_subnet(subnet, parsed_ports)

    # Detect the real default gateway for accurate "gateway" labelling.
    gateway_ip = _detect_gateway(subnet)
    known_by_ip: dict[str, Device] = {}
    for d in db.scalars(select(Device)):
        if d.host:
            known_by_ip[d.host] = d

    nodes: list[dict] = []
    edges: list[dict] = []
    discovered: list[dict] = []

    # Nodes from live scan (online/offline hosts that answered TCP probe).
    scanned_ips = set()

    # Pull the most-recent previous snapshot (if any) to compute "last seen".
    prev_snap = db.scalar(
        select(TopologySnapshot).order_by(TopologySnapshot.scan_time.desc()).limit(1)
    )
    prev_online: dict[str, str] = {}
    if prev_snap:
        try:
            for pn in json.loads(prev_snap.nodes_json):
                if pn.get("status") == "online":
                    prev_online[pn["ip"]] = prev_snap.scan_time.isoformat()
        except Exception:
            pass

    for ip, info in scan.items():
        scanned_ips.add(ip)
        role = "unknown"
        device_id = None
        if ip in known_by_ip:
            role = "shelldeck"
            device_id = known_by_ip[ip].id
        elif gateway_ip and ip == gateway_ip:
            role = "gateway"
        if role != "shelldeck":
            discovered.append({
                "ip": ip,
                "name": info.get("hostname") or f"Host {ip}",
                "hostname": info.get("hostname", ""),
                "os": info.get("device_type", ""),
                "online": info["status"] == "online",
                "ports": info["ports"],
            })
        nodes.append({
            "ip": ip,
            "status": info["status"],
            "ports": info["ports"],
            "role": role,
            "device_id": device_id,
            "device_name": known_by_ip[ip].name if ip in known_by_ip else None,
            "hostname": info.get("hostname", ""),
            "device_type": info.get("device_type", ""),
            "web_title": info.get("web_title", ""),
            "banners": info.get("banners", {}),
            "mac": info.get("mac", ""),
            "vendor": info.get("vendor", ""),
            "latency_ms": info.get("latency_ms"),
            "mdns": info.get("mdns", ""),
            "last_seen": prev_online.get(ip, datetime.now(timezone.utc).isoformat()),
        })

    # Ensure registered ShellDeck devices always appear, even if offline / not probed.
    for ip, dev in known_by_ip.items():
        if ip in scanned_ips:
            continue
        nodes.append({
            "ip": ip,
            "status": "offline",
            "ports": [],
            "role": "shelldeck",
            "device_id": dev.id,
            "device_name": dev.name,
        })

    # When specific ports were requested, only surface hosts that actually match
    # (have at least one of the requested ports open). Gateway and registered
    # ShellDeck devices are always shown as topology anchors.
    if parsed_ports:
        nodes = [
            n for n in nodes
            if n["role"] in ("gateway", "shelldeck") or n["ports"]
        ]
        discovered = [d for d in discovered if d["ports"]]

    online_ips = sorted(n["ip"] for n in nodes if n["status"] == "online")
    gateway_ip = next((n["ip"] for n in nodes if n["role"] == "gateway"), None)

    for n in nodes:
        if n["status"] != "online":
            continue
        if gateway_ip and n["ip"] != gateway_ip:
            edges.append({"from": n["ip"], "to": gateway_ip, "proto": "wan"})
        elif n["ip"] != online_ips[0]:
            edges.append({"from": n["ip"], "to": online_ips[0], "proto": "lan"})

    snap = TopologySnapshot(
        nodes_json=json.dumps(nodes),
        edges_json=json.dumps(edges),
        discovered_json=json.dumps(discovered),
    )
    db.add(snap)
    old = db.scalars(select(TopologySnapshot).order_by(TopologySnapshot.scan_time.asc()).limit(50)).all()
    for o in old[: max(0, len(old) - 10)]:
        db.delete(o)
    db.commit()

    return {
        "subnet": subnet,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "discovered": discovered,
        "snapshot_id": snap.id,
    }


@router.get("/export")
def export_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """Export the user's devices (without secrets — user re-enters creds on import)."""
    devices = list(db.scalars(_visible_devices(db, user)))
    payload = [
        {
            "name": d.name,
            "host": d.host,
            "port": d.port,
            "username": d.username,
            "auth_method": d.auth_method,
            "os": d.os,
            "notes": d.notes,
            "tags": d.tags,
        }
        for d in devices
    ]
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=shelldeck-devices.json"},
    )


@router.get("/inventory/{fmt}")
def inventory_export(fmt: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """Export devices as an Ansible inventory (ini) or Terraform inventory (yaml)."""
    devices = list(db.scalars(_visible_devices(db, user)))
    if fmt == "ansible":
        lines = ["[shelldeck]", ""]
        for d in devices:
            lines.append(f"{d.name} ansible_host={d.host} ansible_port={d.port} ansible_user={d.username}")
        content = "\n".join(lines) + "\n"
        return Response(content=content, media_type="text/plain",
                        headers={"Content-Disposition": "attachment; filename=shelldeck-inventory.ini"})
    if fmt == "terraform":
        hosts = []
        for d in devices:
            hosts.append({
                "name": d.name,
                "connection": d.host,
                "user": d.username,
                "port": d.port,
                "os": d.os or "unknown",
            })
        yaml_block = "hosts = " + json.dumps(hosts, indent=2)
        content = f"# Terraform-style inventory for ShellDeck devices\n{yaml_block}\n"
        return Response(content=content, media_type="text/plain",
                        headers={"Content-Disposition": "attachment; filename=shelldeck-inventory.tf"})
    raise HTTPException(status_code=400, detail="fmt must be 'ansible' or 'terraform'")


@router.post("/import")
def import_devices(payload: list[DeviceCreate], db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Import devices from an export. Secrets must be supplied in each entry.
    Admins import into the shared fleet; operators own their imports.
    (viewer is blocked — only admin/operator may import devices.)"""
    admin = _admin_user(db)
    owner_id = admin.id if (admin and user.role == "admin") else user.id
    created = 0
    for item in payload:
        device = Device(
            owner_id=owner_id,
            name=item.name,
            host=item.host,
            port=item.port,
            username=item.username,
            auth_method=item.auth_method,
            password_enc=encrypt(item.password or ""),
            private_key_enc=encrypt(item.private_key or ""),
            os=item.os,
            notes=item.notes,
            bastion_id=item.bastion_id if hasattr(item, "bastion_id") else None,
            tags=item.tags if hasattr(item, "tags") else "",
            tailscale=item.tailscale if hasattr(item, "tailscale") else False,
        )
        db.add(device)
        created += 1
    db.commit()
    log_audit(db, user, "device_import", f"imported={created}")
    return {"imported": created}


# ----------------------------- Bulk operations -----------------------------
class BulkIds(BaseModel):
    device_ids: list[int] = Field(min_length=1)


class BulkUpdate(BulkIds):
    tags: str | None = None
    notes: str | None = None
    os: str | None = None
    bastion_id: int | None = None


@router.delete("/bulk")
def bulk_delete_devices(payload: BulkIds, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Delete multiple devices at once (own devices for operators; all for admins)."""
    from app.models import SessionLog
    deleted = 0
    for did in payload.device_ids:
        device = db.get(Device, did)
        if device and (user.role == "admin" or device.owner_id == user.id):
            db.query(SessionLog).filter(SessionLog.device_id == device.id).delete()
            db.delete(device)
            deleted += 1
    db.commit()
    log_audit(db, user, "device_bulk_delete", f"deleted={deleted} ids={payload.device_ids}")
    return {"deleted": deleted}


@router.put("/bulk")
def bulk_update_devices(payload: BulkUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Apply a set of fields (tags, notes, os, bastion_id) to owned (or all) devices."""
    updated = 0
    for did in payload.device_ids:
        device = db.get(Device, did)
        if device and (user.role == "admin" or device.owner_id == user.id):
            if payload.tags is not None:
                device.tags = payload.tags
            if payload.notes is not None:
                device.notes = payload.notes
            if payload.os is not None:
                device.os = payload.os
            if payload.bastion_id is not None:
                device.bastion_id = payload.bastion_id
            updated += 1
    db.commit()
    log_audit(db, user, "device_bulk_update", f"updated={updated} ids={payload.device_ids}"
              + (f" bastion_id={payload.bastion_id}" if payload.bastion_id is not None else ""))
    return {"updated": updated}


# ----------------------------- Session audit log -----------------------------
@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user), q: str | None = None) -> list[dict]:
    """List shell sessions (audit trail) for the current user's devices.

    If `q` is provided, filter by device name/host, username, or recorded command text.
    """
    from app.models import SessionLog
    vis = _visible_devices(db, user)
    visible_ids = {d.id for d in db.scalars(vis).all()}
    query = db.query(SessionLog).filter(SessionLog.device_id.in_(visible_ids))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (SessionLog.commands.ilike(like)) |
            (SessionLog.transcript.ilike(like))
        )
    rows = query.order_by(SessionLog.started_at.desc()).limit(500).all()
    out = []
    # Cache device names/hosts so we can also match on them without extra queries.
    dev_cache = {}
    for r in rows:
        dev = dev_cache.get(r.device_id)
        if dev is None:
            dev = db.get(Device, r.device_id)
            dev_cache[r.device_id] = dev
        dev_name = dev.name if dev else "(deleted)"
        dev_host = dev.host if dev else ""
        uname = None
        if r.user_id:
            u = db.get(User, r.user_id)
            uname = u.username if u else None
        if q:
            needle = q.strip().lower()
            if needle not in (dev_name + " " + dev_host + " " + (r.commands or "") + " " + (r.transcript or "") + " " + (uname or "")).lower():
                continue
        out.append({
            "id": r.id,
            "device_id": r.device_id,
            "device_name": dev_name,
            "device_host": dev_host,
            "username": uname,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "duration_s": int((r.ended_at - r.started_at).total_seconds()) if r.ended_at and r.started_at else None,
            "commands": r.commands or "",
            "transcript": r.transcript or "",
            "has_recording": bool(r.recording),
        })
    return out


@router.get("/sessions/{session_id}/recording")
def get_session_recording(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Return the asciinema-style TTY recording for a session (if captured)."""
    from app.models import SessionLog as _SL
    vis = _visible_devices(db, user)
    visible_ids = {d.id for d in db.scalars(vis).all()}
    log = db.get(_SL, session_id)
    if log is None or log.device_id not in visible_ids:
        raise HTTPException(status_code=404, detail="Session not found")
    if not log.recording:
        return {"events": [], "width": 80, "height": 24}
    try:
        rec = json.loads(log.recording)
    except Exception:
        return {"events": [], "width": 80, "height": 24}
    return {"events": rec.get("events", []), "width": rec.get("width", 80), "height": rec.get("height", 24)}


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Device:
    return _to_out(_owned(db, device_id, user), db)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(device_id: int, payload: DeviceUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Device:
    device = _owned(db, device_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "password" and value:
            device.password_enc = encrypt(value)
        elif field == "private_key" and value:
            device.private_key_enc = encrypt(value)
        elif field in ("password", "private_key"):
            continue
        else:
            setattr(device, field, value)
    db.commit()
    db.refresh(device)
    log_audit(db, user, "device_update", f"id={device_id} name={device.name} host={device.host}:{device.port}"
              + (f" bastion_id={device.bastion_id}" if device.bastion_id else ""))
    return _to_out(device, db)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    from app.models import SessionLog
    device = _owned(db, device_id, user)
    db.query(SessionLog).filter(SessionLog.device_id == device.id).delete()
    db.delete(device)
    db.commit()
    log_audit(db, user, "device_delete", f"id={device_id} name={device.name} host={device.host}")


def load_credentials(device: Device) -> tuple[str, str | None, str | None]:
    """Return (username, password, private_key) with decrypted secrets."""
    return device.username, decrypt(device.password_enc) or None, decrypt(device.private_key_enc) or None


def _ssh_opts(device: Device, tunnel: object | None = None) -> dict:
    """Build asyncssh connect options for a device. If `tunnel` (a bastion
    SSHClientConnection) is provided, the connection is routed through it."""
    username, password, private_key = load_credentials(device)
    opts: dict = {
        "host": device.host,
        "port": device.port,
        "username": username,
        "known_hosts": None if settings.ssh_ignore_known_hosts else False,
        "connect_timeout": 10,
    }
    if private_key:
        opts["client_keys"] = [private_key]
    else:
        opts["password"] = password
    if tunnel is not None:
        opts["tunnel"] = tunnel
    return opts


async def connect_device(device: Device, db: Session) -> tuple[object, object | None]:
    """Open an SSH connection to `device`, routing through its bastion if set.

    Returns (conn, bastion_conn). The caller MUST close both (the bastion first
    is not required; closing conn then bastion_conn is safe). If no bastion is
    configured, bastion_conn is None.
    """
    bastion_conn = None
    if device.bastion_id is not None:
        bastion = db.get(Device, device.bastion_id)
        if bastion is not None and bastion.owner_id == device.owner_id:
            bastion_conn = await asyncssh.connect(**_ssh_opts(bastion))
            conn = await asyncssh.connect(**_ssh_opts(device, tunnel=bastion_conn))
            return conn, bastion_conn
    conn = await asyncssh.connect(**_ssh_opts(device))
    return conn, None


async def _probe_reachable(device: Device, db: Session) -> bool:
    """Return True if the device is reachable (directly or via bastion)."""
    try:
        conn, bastion = await connect_device(device, db)
        conn.close()
        if bastion:
            bastion.close()
        return True
    except Exception:
        return False


@router.get("/{device_id}/test")
async def test_connection(device_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Probe SSH connectivity to a device and return reachability + error detail."""
    device = _owned(db, device_id, user)
    try:
        conn, bastion = await connect_device(device, db)
        conn.close()
        if bastion:
            bastion.close()
        return {"ok": True, "message": f"Connected to {device.username}@{device.host}:{device.port}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)[:300]}
