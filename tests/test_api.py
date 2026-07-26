"""Smoke tests for ShellDeck API (no real SSH required)."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_shelldeck.db")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_register_login_flow():
    username = "tester"
    # register (first user -> admin)
    r = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    assert token

    # protected route works with token
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == username

    # protected route rejected without token
    assert client.get("/api/auth/me").status_code == 401


def test_device_crud_requires_auth():
    # no token -> 401
    assert client.get("/api/devices").status_code == 401


def test_device_crud_flow():
    r = client.post("/api/auth/register", json={"username": "devuser", "password": "secret123"})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # create
    d = client.post("/api/devices", headers=h, json={
        "name": "vm-1", "host": "10.0.0.5", "username": "root",
        "auth_method": "password", "password": "topsecret",
    })
    assert d.status_code == 201, d.text
    device_id = d.json()["id"]
    # password must NOT be returned in plaintext
    assert "password" not in d.json()

    # list
    lst = client.get("/api/devices", headers=h)
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    # update
    u = client.put(f"/api/devices/{device_id}", headers=h, json={"notes": "prod box"})
    assert u.status_code == 200
    assert u.json()["notes"] == "prod box"

    # delete
    assert client.delete(f"/api/devices/{device_id}", headers=h).status_code == 204
    assert len(client.get("/api/devices", headers=h).json()) == 0


def test_snippets_flow():
    r = client.post("/api/auth/register", json={"username": "snipuser", "password": "secret123"})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    # create
    c = client.post("/api/snippets", headers=h, json={"name": "uptime", "command": "uptime"})
    assert c.status_code == 201, c.text
    sid = c.json()["id"]
    # list
    lst = client.get("/api/snippets", headers=h)
    assert lst.status_code == 200 and len(lst.json()) == 1
    # delete
    assert client.delete(f"/api/snippets/{sid}", headers=h).status_code == 204


def test_export_import_devices():
    r = client.post("/api/auth/register", json={"username": "io_user", "password": "secret123"})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    client.post("/api/devices", headers=h, json={
        "name": "exp-1", "host": "10.0.0.9", "username": "root", "password": "pw",
    })
    # export
    exp = client.get("/api/devices/export", headers=h)
    assert exp.status_code == 200
    data = exp.json()
    assert len(data) == 1 and data[0]["name"] == "exp-1"
    assert "password" not in data[0]  # secrets omitted
    # import into a fresh user
    r2 = client.post("/api/auth/register", json={"username": "io_user2", "password": "secret123"})
    token2 = r2.json()["access_token"]
    h2 = {"Authorization": f"Bearer {token2}"}
    imp = client.post("/api/devices/import", headers=h2, json=[
        {**data[0], "password": "newpw"},
    ])
    assert imp.status_code == 200, imp.text
    assert imp.json()["imported"] == 1
    after = client.get("/api/devices", headers=h2).json()
    assert after[0]["name"] == "exp-1" and after[0]["host"] == "10.0.0.9"


def test_rbac_roles():
    # First user = admin
    r = client.post("/api/auth/register", json={"username": "rbac_admin", "password": "secret123"})
    admin = r.json()["access_token"]
    ha = {"Authorization": f"Bearer {admin}"}
    # admin can list users and create a viewer
    assert client.get("/api/users", headers=ha).status_code == 200
    c = client.post("/api/users", headers=ha, json={"username": "rbac_viewer", "password": "secret123", "role": "viewer"})
    assert c.status_code == 201, c.text
    vid = c.json()["id"]
    # viewer cannot create devices (403) and cannot list users (403)
    vt = client.post("/api/auth/login", data={"username": "rbac_viewer", "password": "secret123"}).json()["access_token"]
    hv = {"Authorization": f"Bearer {vt}"}
    assert client.post("/api/devices", headers=hv, json={"name": "x", "host": "1.2.3.4", "username": "root", "password": "p"}).status_code == 403
    assert client.get("/api/users", headers=hv).status_code == 403
    # admin promotes viewer -> operator, then operator can create a device
    client.post(f"/api/users/{vid}/role", headers=ha, json={"role": "operator"})
    assert client.post("/api/devices", headers=hv, json={"name": "op-dev", "host": "1.2.3.4", "username": "root", "password": "p"}).status_code == 201
    # admin cannot delete the last admin (itself)
    viewer_id = c.json()["id"]
    assert client.delete(f"/api/users/{viewer_id}", headers=ha).status_code == 204  # remove viewer first
    assert client.delete("/api/users/1", headers=ha).status_code == 400  # would remove last admin


def test_settings_and_scheduled():
    r = client.post("/api/auth/register", json={"username": "set_admin", "password": "secret123"})
    ha = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # settings defaults
    s = client.get("/api/settings", headers=ha)
    assert s.status_code == 200 and "notify_enabled" in s.json()
    # update settings
    u = client.put("/api/settings", headers=ha, json={"notify_enabled": True, "telegram_chat_id": "123", "monitor_interval": 30})
    assert u.status_code == 200 and u.json()["telegram_chat_id"] == "123"
    # test notification endpoint works (no crash)
    assert client.post("/api/settings/test", headers=ha).status_code == 200
    # scheduled tasks
    client.post("/api/devices", headers=ha, json={"name": "d1", "host": "10.0.0.1", "username": "root", "password": "p"})
    st = client.post("/api/scheduled", headers=ha, json={"name": "t1", "command": "uptime", "device_ids": [1], "interval_minutes": 5})
    assert st.status_code == 201, st.text
    lst = client.get("/api/scheduled", headers=ha).json()
    assert len(lst) == 1 and lst[0]["command"] == "uptime"
    # delete
    assert client.delete(f"/api/scheduled/{lst[0]['id']}", headers=ha).status_code == 204


def test_inventory_and_public():
    r = client.post("/api/auth/register", json={"username": "inv_admin", "password": "secret123"})
    ha = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.post("/api/devices", headers=ha, json={"name": "srv1", "host": "10.0.0.5", "username": "root", "password": "p"})
    # inventory ansible
    a = client.get("/api/devices/inventory/ansible", headers=ha)
    assert a.status_code == 200 and "srv1 ansible_host=10.0.0.5" in a.text
    # inventory terraform
    t = client.get("/api/devices/inventory/terraform", headers=ha)
    assert t.status_code == 200 and "srv1" in t.text
    # invalid fmt
    assert client.get("/api/devices/inventory/xml", headers=ha).status_code == 400
    # public dashboard disabled by default -> 404
    assert client.get("/api/public/status").status_code == 404
    # enable public dashboard via settings
    client.put("/api/settings", headers=ha, json={"public_dashboard": True})
    pub = client.get("/api/public/status")
    assert pub.status_code == 200 and pub.json()["total"] >= 1
    # bastion: create two devices, set one as bastion of the other
    b = client.post("/api/devices", headers=ha, json={"name": "bastion", "host": "10.0.0.1", "username": "root", "password": "p"}).json()
    d = client.post("/api/devices", headers=ha, json={"name": "target", "host": "10.0.0.2", "username": "root", "password": "p", "bastion_id": b["id"]}).json()
    assert d["bastion_id"] == b["id"]
    # device out includes bastion_id
    got = client.get(f"/api/devices/{d['id']}", headers=ha).json()
    assert got["bastion_id"] == b["id"]


def test_quick_wins():
    r = client.post("/api/auth/register", json={"username": "qw_admin", "password": "secret123"})
    ha = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # generate-key returns ed25519 keypair
    k = client.get("/api/devices/generate-key", headers=ha).json()
    assert k["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert k["public_key"].startswith("ssh-ed25519 ")
    # test-connection on a device (no device yet -> create one, expect reachable or detailed error)
    dev = client.post("/api/devices", headers=ha, json={"name": "gw", "host": "10.0.0.5", "username": "root", "password": "x"}).json()
    tc = client.get(f"/api/devices/{dev['id']}/test", headers=ha)
    assert tc.status_code == 200 and "ok" in tc.json()
    # sessions endpoint returns a list
    assert isinstance(client.get("/api/devices/sessions", headers=ha).json(), list)
    # snippets export/import roundtrip
    client.post("/api/snippets", headers=ha, json={"name": "uptime", "command": "uptime"})
    snips = client.get("/api/snippets/export", headers=ha).json()
    assert any(s["name"] == "uptime" for s in snips)
    imp = client.post("/api/snippets/import", headers=ha, json=[{"name": "dup", "command": "echo hi"}])
    assert imp.status_code == 200 and imp.json()["imported"] == 1
    # scheduled export/import roundtrip
    st = client.post("/api/scheduled", headers=ha, json={"name": "t", "command": "uptime", "device_ids": [dev["id"]], "interval_minutes": 30})
    assert st.status_code in (200, 201)
    tasks = client.get("/api/scheduled/export", headers=ha).json()
    assert any(x["name"] == "t" for x in tasks)
    imp2 = client.post("/api/scheduled/import", headers=ha, json=[{"name": "t2", "command": "uptime", "device_ids": [], "interval_minutes": 15}])
    assert imp2.status_code == 200 and imp2.json()["imported"] == 1


def test_scheduled_run_once_and_run_now():
    r = client.post("/api/auth/register", json={"username": "ro_admin", "password": "secret123"})
    ha = {"Authorization": f"Bearer {r.json()['access_token']}"}
    dev = client.post("/api/devices", headers=ha, json={"name": "gw", "host": "10.0.0.9", "username": "root", "password": "x"}).json()
    # run_once -> created disabled, no next_run
    t = client.post("/api/scheduled", headers=ha, json={"name": "once", "command": "uptime", "device_ids": [dev["id"]], "interval_minutes": 60, "run_once": True}).json()
    assert t["run_once"] is True and t["enabled"] is False and t["next_run"] is None
    # run_once + run_at(future) -> enabled, scheduled at run_at
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    t2 = client.post("/api/scheduled", headers=ha, json={"name": "later", "command": "uptime", "device_ids": [dev["id"]], "interval_minutes": 60, "run_once": True, "run_at": future}).json()
    assert t2["enabled"] is True and t2["next_run"] is not None
    # run now triggers execution and leaves it disabled
    rn = client.post(f"/api/scheduled/{t['id']}/run", headers=ha)
    assert rn.status_code == 200 and rn.json()["ok"] is True
    got = [x for x in client.get("/api/scheduled", headers=ha).json() if x["id"] == t["id"]][0]
    assert got["enabled"] is False and got["last_run"] is not None
